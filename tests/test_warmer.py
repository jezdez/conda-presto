"""Tests for demand-driven solver cache warming."""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from types import SimpleNamespace

import anyio
import msgspec
import pytest
from conda.models.channel import Channel
from litestar import Litestar

import conda_presto.app as app_module
import conda_presto.cache as cache_module
import conda_presto.warmer as warmer_module
from conda_presto.app import (
    health,
    solver_cache_refresher_lifespan,
    solver_resources_lifespan,
    solver_v1,
)
from conda_presto.broker import conda_broker_services
from conda_presto.cache import (
    ResultCache,
    SolverResultService,
    SolverServiceProbe,
    SolverServiceResult,
    StoredSolverResult,
)
from conda_presto.resolve import RepodataSnapshot
from conda_presto.solver import (
    PrestoSolveError,
    PrestoSolveOutcome,
    PrestoSolverClient,
    PrestoSolveRequest,
    PrestoSolveResponse,
)
from conda_presto.storage import StoreOperationCoordinator
from conda_presto.warm_candidates import SolverWarmCandidates
from conda_presto.warmer import ForegroundCapacity, SolverCacheWarmer


class RecordingWorker:
    def __init__(
        self,
        *,
        start_error: Exception | None = None,
        ready: bool = True,
    ) -> None:
        self.start_error = start_error
        self.ready = ready
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1
        self.running = True
        if self.start_error is not None:
            self.ready = False
            self.running = False
            raise self.start_error

    def shutdown(self) -> None:
        self.stops += 1
        self.ready = False
        self.running = False


class RecordingService:
    def __init__(
        self,
        probes: list[SolverServiceProbe],
        results: list[SolverServiceResult | Exception] | None = None,
        *,
        cache_size: int = 32,
        persistent: bool = False,
    ) -> None:
        self.cache = SimpleNamespace(
            max_size=cache_size,
            store_operations=object() if persistent else None,
        )
        self.probes = probes
        self.results = results or []
        self.inspect_calls: list[PrestoSolveRequest] = []
        self.resolve_calls: list[tuple[PrestoSolveRequest, RecordingWorker, float]] = []

    async def inspect(self, request: PrestoSolveRequest) -> SolverServiceProbe:
        self.inspect_calls.append(request)
        return self.probes.pop(0)

    async def resolve(
        self,
        request: PrestoSolveRequest,
        worker: RecordingWorker,
        deadline: float,
    ) -> SolverServiceResult:
        self.resolve_calls.append((request, worker, deadline))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture()
def successful_outcome(fresh_repodata_snapshot):
    response = PrestoSolveResponse(records=[], neutered=[])
    return PrestoSolveOutcome(
        result=response,
        metadata_before=fresh_repodata_snapshot,
        metadata_used=fresh_repodata_snapshot,
    )


@pytest.fixture()
def successful_result(successful_outcome):
    return SolverServiceResult(
        result=successful_outcome.result,
        disposition="published",
    )


@pytest.fixture()
def record_candidate_request():
    def observe(
        warm_candidates: SolverWarmCandidates,
        request: PrestoSolveRequest,
        *,
        now: float | None = None,
    ) -> str:
        warm_candidates.record(request, now=now)
        warm_candidates.record(request, now=now)
        return request.warming_key()

    return observe


@pytest.fixture()
def create_warmer(monkeypatch):
    def create(
        warm_candidates: SolverWarmCandidates,
        service: RecordingService,
        *,
        limiter: ForegroundCapacity | None = None,
        worker: RecordingWorker | None = None,
        interval_s: float = 300,
        batch_size: int = 8,
    ) -> SolverCacheWarmer:
        warmer = SolverCacheWarmer(
            warm_candidates=warm_candidates,
            service=service,
            limiter=limiter or ForegroundCapacity(anyio.CapacityLimiter(1)),
            interval_s=interval_s,
            batch_size=batch_size,
        )
        if worker is not None:
            monkeypatch.setattr(
                warmer_module,
                "PersistentSolveWorker",
                lambda *_args, **_kwargs: worker,
            )
        return warmer

    return create


@pytest.fixture()
def enable_cache_warming(monkeypatch):
    for name, value in {
        "PERSISTENT_WORKER": True,
        "RESULT_CACHE_BACKEND": "memory",
        "RESULT_CACHE_SIZE": 8,
        "SOLVER_CACHE_WARM_CANDIDATE_SIZE": 8,
        "SOLVER_CACHE_WARM_BATCH_SIZE": 2,
        "SOLVER_CACHE_WARM_INTERVAL_S": 300,
    }.items():
        monkeypatch.setattr(app_module, name, value)
    monkeypatch.setenv(
        "CONDA_BROKER_SERVICE_NAME",
        PrestoSolverClient.service_name,
    )


@pytest.mark.anyio
async def test_foreground_capacity_tracks_active_arrivals():
    capacity = ForegroundCapacity(anyio.CapacityLimiter(1))

    assert capacity.idle_generation() == 0

    limiter = capacity.arrive()
    await limiter.acquire()

    assert capacity.generation == 1
    assert limiter.borrowed_tokens == 1
    assert capacity.idle_generation() is None

    limiter.release()

    assert capacity.idle_generation() == 1


@pytest.mark.anyio
async def test_foreground_capacity_tracks_waiting_arrivals():
    capacity = ForegroundCapacity(anyio.CapacityLimiter(1))
    limiter = capacity.limiter
    acquired = anyio.Event()
    release_waiter = anyio.Event()

    async def wait_for_token() -> None:
        async with capacity.arrive():
            acquired.set()
            await release_waiter.wait()

    await capacity.arrive().acquire()
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(wait_for_token)
        while limiter.statistics().tasks_waiting == 0:
            await anyio.lowlevel.checkpoint()

        assert capacity.generation == 2
        assert limiter.statistics().tasks_waiting == 1
        assert capacity.idle_generation() is None

        limiter.release()
        await acquired.wait()
        assert capacity.idle_generation() is None
        release_waiter.set()

    assert capacity.idle_generation() == 2


@pytest.mark.anyio
async def test_solver_service_inspects_hits_misses_and_metadata_failures(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
):
    cache = ResultCache(max_size=8)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    cache.remember_memory(
        key,
        StoredSolverResult(
            response=PrestoSolveResponse(records=[], neutered=[]),
            metadata_used=fresh_repodata_snapshot,
        ),
    )
    service = SolverResultService(cache)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata_snapshot,
    )

    hit = await service.inspect(presto_solver_request)
    assert hit.cached
    assert hit.current == fresh_repodata_snapshot

    cache.entries.clear()
    cache.current_bytes = 0
    miss = await service.inspect(presto_solver_request)
    assert not miss.cached
    assert miss.current == fresh_repodata_snapshot

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: (_ for _ in ()).throw(RuntimeError),
    )
    failed = await service.inspect(presto_solver_request)
    assert not failed.cached
    assert failed.current is None


@pytest.mark.anyio
async def test_solver_service_offloads_advisory_metadata(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
):
    caller_thread = threading.get_ident()
    metadata_threads = []

    def capture_metadata(_request):
        metadata_threads.append(threading.get_ident())
        return fresh_repodata_snapshot

    monkeypatch.setattr(PrestoSolveRequest, "repodata_snapshot", capture_metadata)

    probe = await SolverResultService(ResultCache(max_size=8)).inspect(
        presto_solver_request
    )

    assert probe.current == fresh_repodata_snapshot
    assert metadata_threads
    assert metadata_threads != [caller_thread]


@pytest.mark.anyio
async def test_warm_service_does_not_use_the_default_thread_limiter(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_outcome,
):
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata_snapshot,
    )
    default_limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = default_limiter.total_tokens
    started = threading.Event()
    release = threading.Event()

    def occupy_default_thread() -> None:
        started.set()
        release.wait()

    async def occupy() -> None:
        await anyio.to_thread.run_sync(occupy_default_thread)

    worker = SimpleNamespace(
        solve_final_state=lambda *_: successful_outcome,
    )
    service = SolverResultService(
        ResultCache(max_size=8),
        thread_limiter=anyio.CapacityLimiter(1),
    )
    default_limiter.total_tokens = 1
    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(occupy)
            while not started.is_set():
                await anyio.lowlevel.checkpoint()
            try:
                with anyio.fail_after(0.5):
                    result = await service.resolve(presto_solver_request, worker, 30)
            finally:
                release.set()
    finally:
        default_limiter.total_tokens = original_tokens

    assert result.disposition == "published"


@pytest.mark.anyio
async def test_persistent_store_timeout_is_bounded(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_outcome,
    record_candidate_request,
):
    store_write_started = anyio.Event()
    release_store_write = anyio.Event()

    class HangingStore:
        async def get(self, _key):
            return None

        async def set(self, _key, _value, expires_in=None):
            del expires_in
            store_write_started.set()
            await release_store_write.wait()

        async def delete(self, _key):
            await anyio.sleep_forever()

    class SolvingWorker(RecordingWorker):
        def solve_final_state(self, *_):
            return successful_outcome

    monkeypatch.setattr(cache_module, "RESULT_CACHE_STORE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(warmer_module, "SOLVER_CACHE_WARM_INITIAL_DELAY_S", 0)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata_snapshot,
    )
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    thread_limiter = anyio.CapacityLimiter(1)
    store = HangingStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=8, store_operations=store_operations)
    warmer = SolverCacheWarmer(
        warm_candidates=warm_candidates,
        service=SolverResultService(
            cache=cache,
            thread_limiter=thread_limiter,
            require_persistent=True,
        ),
        limiter=ForegroundCapacity(anyio.CapacityLimiter(1)),
        interval_s=10,
        batch_size=1,
        thread_limiter=thread_limiter,
    )
    worker = SolvingWorker()
    monkeypatch.setattr(
        warmer_module,
        "PersistentSolveWorker",
        lambda *_args, **_kwargs: worker,
    )

    stop = anyio.Event()
    async with store_operations.lifespan():
        with anyio.fail_after(0.5):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(warmer.run, stop)
                await store_write_started.wait()
                while warmer.stats.rejected_publications == 0:
                    await anyio.lowlevel.checkpoint()
                stop.set()
                release_store_write.set()

    assert worker.starts == 1
    assert worker.stops == 1
    assert ResultCache.solver_key(presto_solver_request.cache_key()) in cache.entries
    assert fingerprint in warm_candidates.entries
    assert warmer.stats.rejected_publications == 1


@pytest.mark.anyio
async def test_warm_cycle_with_no_candidates_does_not_create_worker(
    monkeypatch,
    create_warmer,
):
    service = RecordingService([])
    warmer = create_warmer(SolverWarmCandidates(max_size=32), service)

    def fail_create(*_args, **_kwargs):
        pytest.fail("empty cycle created a worker")

    monkeypatch.setattr(warmer_module, "PersistentSolveWorker", fail_create)

    await warmer.cycle()

    assert warmer.stats.cycles == 1
    assert service.inspect_calls == []
    assert warmer.active_worker is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("batch_size", "cache_size", "persistent", "expected"),
    [
        pytest.param(1, 3, False, 1, id="batch-bound"),
        pytest.param(3, 1, False, 1, id="memory-cache-bound"),
        pytest.param(3, 1, True, 3, id="persistent-cache-not-bound"),
    ],
)
async def test_refresh_cycle_selection_respects_storage_limits(
    batch_size,
    cache_size,
    persistent,
    expected,
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    for name in ("a", "b", "c"):
        request = msgspec.structs.replace(presto_solver_request, specs_to_add=[name])
        record_candidate_request(warm_candidates, request)
    service = RecordingService(
        [SolverServiceProbe(cached=True, current=fresh_repodata_snapshot)] * expected,
        cache_size=cache_size,
        persistent=persistent,
    )
    warmer = create_warmer(warm_candidates, service, batch_size=batch_size)

    await warmer.cycle()

    assert len(service.inspect_calls) == expected
    assert warmer.stats.already_current == expected


@pytest.mark.anyio
async def test_fresh_cache_hit_does_not_create_worker(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=True, current=fresh_repodata_snapshot)]
    )
    warmer = create_warmer(warm_candidates, service)

    def fail_create(*_args, **_kwargs):
        pytest.fail("fresh cache hit created a worker")

    monkeypatch.setattr(warmer_module, "PersistentSolveWorker", fail_create)

    await warmer.cycle()

    assert service.resolve_calls == []
    assert warmer.stats.already_current == 1


@pytest.mark.anyio
async def test_missing_cache_entry_is_replayed(
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)],
        [successful_result],
    )
    worker = RecordingWorker()
    warmer = create_warmer(warm_candidates, service, worker=worker)

    await warmer.cycle()

    assert len(service.resolve_calls) == 1
    request, recorded_worker, deadline = service.resolve_calls[0]
    assert request == presto_solver_request
    assert recorded_worker is worker
    assert (
        0
        < deadline - warmer_module.time.monotonic()
        <= min(
            warmer_module.SOLVE_TIMEOUT_S,
            warmer_module.SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
        )
    )
    assert worker.starts == 1
    assert worker.stops == 1
    assert warmer.active_worker is None
    assert warmer.stats.successful_refreshes == 1


@pytest.mark.anyio
async def test_warmer_uses_service_with_one_worker_reused_and_cleaned_up(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(presto_solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        record_candidate_request(warm_candidates, request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)] * 2,
        [successful_result, successful_result],
    )
    worker = RecordingWorker()
    constructor_calls = []

    def create_worker(channels, platforms, **kwargs):
        constructor_calls.append((channels, platforms, kwargs))
        return worker

    monkeypatch.setattr(warmer_module, "PersistentSolveWorker", create_worker)
    warmer = create_warmer(warm_candidates, service)
    generation = warmer.limiter.generation

    await warmer.cycle()

    assert constructor_calls == [
        (
            [],
            [],
            {
                "restart_on_failure": False,
                "startup_timeout_s": min(
                    warmer_module.SOLVE_TIMEOUT_S,
                    warmer_module.SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
                ),
                "warmup_on_start": False,
                "log_worker_errors": False,
            },
        )
    ]
    assert worker.starts == 1
    assert worker.stops == 1
    assert not hasattr(worker, "solve_final_state")
    assert len(service.resolve_calls) == 2
    assert {call[1] for call in service.resolve_calls} == {worker}
    assert warmer.limiter.generation == generation
    assert warmer.limiter.limiter.borrowed_tokens == 0


@pytest.mark.anyio
async def test_active_foreground_work_stops_cycle_before_cache_inspection(
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)]
    )
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))
    warmer = create_warmer(warm_candidates, service, limiter=limiter)

    await limiter.arrive().acquire()
    try:
        await warmer.cycle()
    finally:
        limiter.limiter.release()

    assert service.inspect_calls == []
    assert warmer.stats.foreground_skips == 1


@pytest.mark.anyio
async def test_waiting_foreground_work_stops_cycle_before_cache_inspection(
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)]
    )
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))
    warmer = create_warmer(warm_candidates, service, limiter=limiter)
    waiter_acquired = anyio.Event()

    async def wait_for_token() -> None:
        async with limiter.arrive():
            waiter_acquired.set()

    await limiter.arrive().acquire()
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(wait_for_token)
        while limiter.limiter.statistics().tasks_waiting == 0:
            await anyio.lowlevel.checkpoint()
        await warmer.cycle()
        limiter.limiter.release()
        await waiter_acquired.wait()

    assert service.inspect_calls == []
    assert warmer.stats.foreground_skips == 1


@pytest.mark.anyio
async def test_foreground_arrival_during_solve_stops_further_work(
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(presto_solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        record_candidate_request(warm_candidates, request)
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))

    class ArrivingService(RecordingService):
        async def resolve(self, request, worker, deadline):
            result = await super().resolve(request, worker, deadline)
            await limiter.arrive().acquire()
            limiter.limiter.release()
            return result

    service = ArrivingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)] * 2,
        [successful_result, successful_result],
    )
    worker = RecordingWorker()
    warmer = create_warmer(
        warm_candidates,
        service,
        limiter=limiter,
        worker=worker,
    )

    await warmer.cycle()

    assert len(service.inspect_calls) == 1
    assert len(service.resolve_calls) == 1
    assert warmer.stats.foreground_skips == 1
    assert worker.stops == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "inspection",
    [
        pytest.param("timeout", id="timeout"),
        pytest.param("metadata", id="metadata-unavailable"),
        pytest.param("persistence", id="persistence-failed"),
        pytest.param("local", id="local-source"),
        pytest.param("cached", id="cached"),
    ],
)
async def test_foreground_arrival_during_inspection_outcomes_stops_cycle(
    inspection,
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(presto_solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        record_candidate_request(warm_candidates, request)
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))
    current = (
        RepodataSnapshot(
            (("file:///srv/channel/linux-64", "repodata.json", 10, 1),),
            False,
        )
        if inspection == "local"
        else fresh_repodata_snapshot
    )

    class ArrivingInspectionService(RecordingService):
        async def inspect(self, request):
            self.inspect_calls.append(request)
            await limiter.arrive().acquire()
            limiter.limiter.release()
            if inspection == "timeout":
                raise TimeoutError
            return SolverServiceProbe(
                cached=inspection == "cached",
                current=None if inspection == "metadata" else current,
                persistence_failed=inspection == "persistence",
            )

    service = ArrivingInspectionService([])
    warmer = create_warmer(warm_candidates, service, limiter=limiter)

    await warmer.cycle()

    assert len(service.inspect_calls) == 1
    assert service.resolve_calls == []
    assert warmer.active_worker is None
    assert warmer.stats.foreground_skips == 1
    fingerprint = service.inspect_calls[0].warming_key()
    assert (fingerprint not in warm_candidates.entries) is (inspection == "local")


@pytest.mark.anyio
async def test_cycle_budget_stops_before_next_candidate(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    for name in ("a", "b"):
        record_candidate_request(
            warm_candidates,
            msgspec.structs.replace(presto_solver_request, specs_to_add=[name]),
        )
    service = RecordingService(
        [SolverServiceProbe(cached=True, current=fresh_repodata_snapshot)] * 2
    )
    clock = iter(
        [
            0,
            0,
            0,
            warmer_module.SOLVER_CACHE_WARM_CYCLE_BUDGET_S + 1,
        ]
    )
    monkeypatch.setattr(
        warmer_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    warmer = create_warmer(warm_candidates, service)

    await warmer.cycle()

    assert len(service.inspect_calls) == 1


@pytest.mark.anyio
async def test_cycle_does_not_start_worker_after_inspection_uses_budget(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    clock = {"now": 0.0}

    class SlowInspectionService(RecordingService):
        async def inspect(self, request):
            probe = await super().inspect(request)
            clock["now"] = warmer_module.SOLVER_CACHE_WARM_CYCLE_BUDGET_S + 1
            return probe

    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    service = SlowInspectionService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)]
    )
    monkeypatch.setattr(
        warmer_module,
        "time",
        SimpleNamespace(monotonic=lambda: clock["now"]),
    )
    warmer = create_warmer(warm_candidates, service)

    def fail_create(*_args, **_kwargs):
        pytest.fail("worker started after the cycle budget")

    monkeypatch.setattr(warmer_module, "PersistentSolveWorker", fail_create)

    await warmer.cycle()

    assert service.inspect_calls == [presto_solver_request]
    assert service.resolve_calls == []


@pytest.mark.anyio
async def test_solver_error_discards_candidate(
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    error = PrestoSolveError(kind="unsatisfiable", message="conflict")
    result = SolverServiceResult(
        result=error,
        disposition="solver-error",
    )
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)],
        [result],
    )
    warmer = create_warmer(
        warm_candidates,
        service,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    assert fingerprint not in warm_candidates.entries
    assert warmer.stats.failures == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("same_request", "retained"),
    [
        pytest.param(False, False, id="unrelated-request"),
        pytest.param(True, True, id="same-request"),
    ],
)
async def test_solver_error_preserves_only_concurrent_same_request(
    same_request,
    retained,
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    initial = warm_candidates.entries[fingerprint]
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))
    error = SolverServiceResult(
        result=PrestoSolveError(kind="unsatisfiable", message="conflict"),
        disposition="solver-error",
    )

    class RecordingArrivalService(RecordingService):
        async def resolve(self, request, worker, deadline):
            await limiter.arrive().acquire()
            limiter.limiter.release()
            recorded = (
                request
                if same_request
                else msgspec.structs.replace(request, specs_to_add=["unrelated"])
            )
            warm_candidates.record(recorded, now=initial.last_requested + 1)
            return await super().resolve(request, worker, deadline)

    service = RecordingArrivalService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)],
        [error],
    )
    warmer = create_warmer(
        warm_candidates,
        service,
        limiter=limiter,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    assert (fingerprint in warm_candidates.entries) is retained
    assert warmer.stats.failures == 1
    assert warmer.stats.foreground_skips == 1


@pytest.mark.anyio
async def test_metadata_failure_keeps_candidate_without_starting_worker(
    presto_solver_request,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    service = RecordingService([SolverServiceProbe(cached=False, current=None)])
    warmer = create_warmer(warm_candidates, service)

    await warmer.cycle()

    assert fingerprint in warm_candidates.entries
    assert warmer.stats.failures == 1
    assert warmer.active_worker is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stage", "error", "counter"),
    [
        pytest.param("start", TimeoutError(), "timeouts", id="startup-timeout"),
        pytest.param(
            "start",
            RuntimeError("startup failed"),
            "failures",
            id="startup-failure",
        ),
        pytest.param("solve", TimeoutError(), "timeouts", id="solve-timeout"),
        pytest.param("solve", RuntimeError("worker died"), "failures", id="death"),
    ],
)
async def test_worker_timeout_or_death_keeps_candidate_and_stops_cycle(
    stage,
    error,
    counter,
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    worker = RecordingWorker(
        start_error=error if stage == "start" else None,
        ready=stage != "solve" or not isinstance(error, RuntimeError),
    )
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)],
        [error] if stage == "solve" else [],
    )
    warmer = create_warmer(warm_candidates, service, worker=worker)

    await warmer.cycle()

    assert getattr(warmer.stats, counter) == 1
    assert fingerprint in warm_candidates.entries
    assert worker.stops == 1
    assert warmer.active_worker is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "disposition",
    ["publication-rejected", "not-retained"],
)
async def test_rejected_publication_keeps_candidate(
    disposition,
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    result = replace(successful_result, disposition=disposition)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)],
        [result],
    )
    warmer = create_warmer(
        warm_candidates,
        service,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    assert warmer.stats.rejected_publications == 1
    assert fingerprint in warm_candidates.entries


@pytest.mark.anyio
async def test_file_source_is_discarded_without_replay(
    presto_solver_request,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, presto_solver_request)
    local = RepodataSnapshot(
        (("file:///srv/channel/linux-64", "repodata.json", 10, 1),),
        False,
    )
    service = RecordingService([SolverServiceProbe(cached=False, current=local)])
    warmer = create_warmer(warm_candidates, service)

    await warmer.cycle()

    assert fingerprint not in warm_candidates.entries
    assert service.resolve_calls == []
    assert warmer.active_worker is None


@pytest.mark.anyio
async def test_scheduler_contains_failure_and_stops(monkeypatch, create_warmer):
    monkeypatch.setattr(warmer_module, "SOLVER_CACHE_WARM_INITIAL_DELAY_S", 0)
    warmer = create_warmer(
        SolverWarmCandidates(max_size=32),
        RecordingService([]),
        interval_s=0.001,
    )
    stop = anyio.Event()
    cycles = 0

    async def cycle(_stop):
        nonlocal cycles
        cycles += 1
        if cycles == 1:
            raise RuntimeError("cycle failed")
        stop.set()

    warmer.cycle = cycle

    with anyio.fail_after(0.5):
        await warmer.run(stop)

    assert warmer.stats.failures == 1
    assert cycles == 2


@pytest.mark.anyio
async def test_warmer_lifespan_waits_for_cleanup_before_checkpoint(monkeypatch):
    events = []
    started = anyio.Event()
    finished = anyio.Event()
    foreground_worker = SimpleNamespace(
        ready=True,
        running=True,
        start=lambda: events.append("foreground-worker-start"),
        shutdown=lambda: events.append("foreground-worker-stop"),
    )

    class RecordingStore:
        async def __aenter__(self):
            events.append("store-open")
            return self

        async def __aexit__(self, *_):
            events.append("store-close")

    store = RecordingStore()

    class WarmCandidates:
        async def load(self):
            events.append("load")

        async def checkpoint(self):
            assert finished.is_set()
            events.append("checkpoint")

    warmer_options = {}

    class Warmer:
        def __init__(self, **options):
            warmer_options.update(options)

        async def run(self, stop):
            events.append("scheduler-start")
            started.set()
            await stop.wait()
            events.append("warm-worker-stop")
            finished.set()

    warm_candidates = WarmCandidates()
    warm_candidate_options = {}

    def create_warm_candidates(**options):
        warm_candidate_options.update(options)
        return warm_candidates

    monkeypatch.setattr(app_module, "SolverWarmCandidates", create_warm_candidates)
    monkeypatch.setattr(app_module, "SolverCacheWarmer", Warmer)
    monkeypatch.setattr(
        app_module,
        "PersistentSolveWorker",
        lambda *_args, **_kwargs: foreground_worker,
    )
    monkeypatch.setattr(
        app_module,
        "shutdown_process_pool",
        lambda: events.append("process-pool-stop"),
    )
    monkeypatch.setenv(
        "CONDA_BROKER_SERVICE_NAME",
        PrestoSolverClient.service_name,
    )
    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", True)
    monkeypatch.setattr(app_module, "RESULT_CACHE_BACKEND", "file")
    monkeypatch.setattr(
        ResultCache,
        "store_for_config",
        staticmethod(lambda *_args: store),
    )
    monkeypatch.setattr(app_module, "SOLVER_CACHE_WARM_INTERVAL_S", 300)
    monkeypatch.setattr(app_module, "SOLVER_CACHE_WARM_BATCH_SIZE", 8)
    monkeypatch.setattr(app_module, "SOLVER_CACHE_WARM_CANDIDATE_SIZE", 32)
    monkeypatch.setattr(app_module, "RESULT_CACHE_SIZE", 0)
    app = Litestar(
        route_handlers=[health],
        lifespan=[
            solver_resources_lifespan,
            solver_cache_refresher_lifespan,
        ],
    )

    async with app.lifespan():
        await started.wait()
        events.append("foreground-ready")
        assert app.state.solve_worker is foreground_worker
        assert foreground_worker.ready

    assert finished.is_set()
    assert app.state.solve_worker is foreground_worker
    assert (
        warm_candidate_options["store_operations"]
        is app.state.result_cache.store_operations
    )
    assert warm_candidate_options["store_operations"].store is store
    assert (
        warmer_options["thread_limiter"] is not warmer_options["service"].thread_limiter
    )
    assert events == [
        "store-open",
        "foreground-worker-start",
        "load",
        "scheduler-start",
        "foreground-ready",
        "warm-worker-stop",
        "checkpoint",
        "foreground-worker-stop",
        "process-pool-stop",
        "store-close",
    ]


@pytest.mark.anyio
async def test_cache_refresh_requires_broker_service_identity(
    monkeypatch,
    enable_cache_warming,
):
    started = False

    async def run(_self, stop):
        nonlocal started
        started = True
        await stop.wait()

    monkeypatch.delenv("CONDA_BROKER_SERVICE_NAME")
    monkeypatch.setattr(SolverCacheWarmer, "run", run)
    app = Litestar(route_handlers=[health])
    app.state.result_cache = ResultCache(max_size=8)
    app.state.solver_limiter = ForegroundCapacity(anyio.CapacityLimiter(1))

    async with solver_cache_refresher_lifespan(app):
        await anyio.lowlevel.checkpoint()

    assert not started


@pytest.mark.anyio
async def test_broker_cycle_keeps_foreground_solver_ready(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_outcome,
):
    service = next(conda_broker_services())
    assert service.process is not None
    assert service.process.env["CONDA_PRESTO_PERSISTENT_WORKER"] == "1"
    workers = []

    class SolvingWorker:
        def __init__(self, role):
            self.role = role
            self.ready = False
            self.running = False
            self.solve_calls = 0
            self.stops = 0

        def start(self):
            self.ready = True
            self.running = True

        def shutdown(self):
            self.ready = False
            self.running = False
            self.stops += 1

        def recover_if_stopped(self):
            return None

        def solve_final_state(self, *_):
            self.solve_calls += 1
            return successful_outcome

    def create_worker(_channels, _platforms, **kwargs):
        role = "warm" if kwargs.get("warmup_on_start") is False else "foreground"
        worker = SolvingWorker(role)
        workers.append(worker)
        return worker

    for name, value in {
        "PERSISTENT_WORKER": True,
        "RESULT_CACHE_BACKEND": "memory",
        "RESULT_CACHE_SIZE": 8,
        "SOLVER_CACHE_WARM_CANDIDATE_SIZE": 8,
        "SOLVER_CACHE_WARM_BATCH_SIZE": 1,
        "SOLVER_CACHE_WARM_INTERVAL_S": 300,
    }.items():
        monkeypatch.setattr(app_module, name, value)
    monkeypatch.setenv(
        "CONDA_BROKER_SERVICE_NAME",
        PrestoSolverClient.service_name,
    )
    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
    monkeypatch.setattr(warmer_module, "PersistentSolveWorker", create_worker)
    monkeypatch.setattr(app_module, "shutdown_process_pool", lambda: None)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata_snapshot,
    )
    app = Litestar(route_handlers=[health, solver_v1])

    async with solver_resources_lifespan(app):
        async with solver_cache_refresher_lifespan(app):
            foreground_worker = workers[0]
            app.state.solver_warm_candidates.record(presto_solver_request)
            app.state.solver_warm_candidates.record(presto_solver_request)

            await app.state.solver_cache_refresher.cycle()

            request = SimpleNamespace(
                app=app,
                client=SimpleNamespace(host="127.0.0.1"),
            )
            response = await solver_v1.fn(request, presto_solver_request)
            readiness = await health.fn(request)

            warm_worker = workers[1]
            assert response.content == successful_outcome.result
            assert readiness.content.status == "ok"
            assert foreground_worker.ready
            assert foreground_worker.solve_calls == 0
            assert warm_worker.solve_calls == 1
            assert warm_worker.stops == 1

    assert workers[0].stops == 1


@pytest.mark.anyio
async def test_cycle_cancellation_still_stops_dedicated_worker(
    presto_solver_request,
    fresh_repodata_snapshot,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    resolving = anyio.Event()

    class CancelledService(RecordingService):
        async def resolve(self, request, worker, deadline):
            resolving.set()
            await anyio.sleep_forever()

    service = CancelledService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)]
    )
    worker = RecordingWorker()
    warmer = create_warmer(warm_candidates, service, worker=worker)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(warmer.cycle)
        await resolving.wait()
        tasks.cancel_scope.cancel()

    assert worker.starts == 1
    assert worker.stops == 1
    assert warmer.active_worker is None


@pytest.mark.anyio
async def test_incomplete_worker_cleanup_retains_handle(
    presto_solver_request,
    fresh_repodata_snapshot,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    class UnstoppableWorker(RecordingWorker):
        def shutdown(self):
            super().shutdown()
            return False

    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, presto_solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata_snapshot)],
        [successful_result],
    )
    worker = UnstoppableWorker()
    warmer = create_warmer(warm_candidates, service, worker=worker)

    await warmer.cycle()

    assert warmer.active_worker is worker
    assert warmer.stats.failures == 1


@pytest.mark.anyio
async def test_logs_contain_only_aggregate_state_and_no_request_data(
    caplog,
    presto_solver_request,
    record_candidate_request,
    create_warmer,
):
    secret_request = msgspec.structs.replace(
        presto_solver_request,
        specs_to_add=["secret-package"],
        channels=[
            {
                **Channel("conda-forge").dump(),
                "token": "super-secret-token",
            }
        ],
    )
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, secret_request)
    service = RecordingService([SolverServiceProbe(cached=False, current=None)])
    warmer = create_warmer(warm_candidates, service)

    with caplog.at_level(logging.INFO, logger="conda_presto.warmer"):
        await warmer.cycle()

    fingerprint = secret_request.warming_key()
    assert "secret-package" not in caplog.text
    assert "super-secret-token" not in caplog.text
    assert fingerprint[:12] in caplog.text
    assert fingerprint not in caplog.text
    summary = next(
        record.solver_cache_refresh
        for record in caplog.records
        if record.getMessage().startswith("Solver cache refresh cycle ")
    )
    assert "cycles=1" in caplog.text
    assert set(summary) == {
        "selected",
        "recorded_requests",
        "cycles",
        "already_current",
        "attempts",
        "successful_refreshes",
        "foreground_skips",
        "timeouts",
        "failures",
        "rejected_publications",
    }
    assert all(type(value) is int for value in summary.values())
    assert (
        not {
            "channels",
            "specs",
            "request",
            "fingerprint",
        }
        & summary.keys()
    )
