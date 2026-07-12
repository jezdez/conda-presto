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
from conda_presto.app import (
    SOLVER_CACHE_WARM_INITIAL_DELAY_S,
    ForegroundCapacity,
    ResultCache,
    SolverCacheWarmer,
    SolverResultService,
    SolverServiceProbe,
    SolverServiceResult,
    StoredSolverResult,
    health,
    solver_cache_refresher_lifespan,
    solver_resources_lifespan,
    solver_v1,
)
from conda_presto.broker import conda_broker_services
from conda_presto.resolve import RepodataSnapshot
from conda_presto.solver import (
    PrestoSolveError,
    PrestoSolveOutcome,
    PrestoSolveRequest,
    PrestoSolveResponse,
)
from conda_presto.storage import StoreOperationCoordinator
from conda_presto.warm_candidates import SolverWarmCandidates


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

    def stop(self) -> None:
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
        self.resolve_calls: list[tuple[PrestoSolveRequest, RecordingWorker, int]] = []

    async def inspect(self, request: PrestoSolveRequest) -> SolverServiceProbe:
        self.inspect_calls.append(request)
        return self.probes.pop(0)

    async def resolve(
        self,
        request: PrestoSolveRequest,
        worker: RecordingWorker,
        timeout_s: int,
    ) -> SolverServiceResult:
        self.resolve_calls.append((request, worker, timeout_s))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture()
def solver_request():
    return PrestoSolveRequest(
        channels=[Channel("conda-forge").dump()],
        subdirs=["linux-64", "noarch"],
        specs_to_add=["zlib"],
        specs_to_remove=[],
        installed=[],
        history=[],
        pinned=[],
        virtual=[],
        aggressive_updates=[],
        always_update=[],
        update_modifier="UPDATE_SPECS",
        deps_modifier="NOT_SET",
        ignore_pinned=False,
        force_remove=False,
        prune=False,
        command="install",
        repodata_fn="repodata.json",
        offline=False,
        channel_priority="strict",
        use_only_tar_bz2=False,
        add_pip_as_python_dependency=True,
        allow_cycles=True,
        restore_free_channel=False,
        repodata_use_shards=True,
        use_index_cache=False,
    )


@pytest.fixture()
def fresh_repodata():
    return RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )


@pytest.fixture()
def successful_result(fresh_repodata):
    response = PrestoSolveResponse(records=[], neutered=[])
    outcome = PrestoSolveOutcome(
        result=response,
        metadata_before=fresh_repodata,
        metadata_used=fresh_repodata,
    )
    return SolverServiceResult(
        result=response,
        disposition="published",
        metadata_used=fresh_repodata,
        outcome=outcome,
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
def create_warmer():
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
            warmer.create_worker = lambda: worker
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
        "SOLVER_ENDPOINT": True,
    }.items():
        monkeypatch.setattr(app_module, name, value)


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
    solver_request,
    fresh_repodata,
):
    cache = ResultCache(max_size=8)
    key = ResultCache.solver_key(solver_request.cache_key())
    cache.remember_memory(
        key,
        StoredSolverResult(
            response=PrestoSolveResponse(records=[], neutered=[]),
            metadata_used=fresh_repodata,
        ),
    )
    service = SolverResultService(cache)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata,
    )

    hit = await service.inspect(solver_request)
    assert hit.cached
    assert hit.current == fresh_repodata

    cache.entries.clear()
    cache.current_bytes = 0
    miss = await service.inspect(solver_request)
    assert not miss.cached
    assert miss.current == fresh_repodata

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: (_ for _ in ()).throw(RuntimeError),
    )
    failed = await service.inspect(solver_request)
    assert not failed.cached
    assert failed.current is None


@pytest.mark.anyio
async def test_solver_service_offloads_advisory_metadata(
    monkeypatch,
    solver_request,
    fresh_repodata,
):
    caller_thread = threading.get_ident()
    metadata_threads = []

    def capture_metadata(_request):
        metadata_threads.append(threading.get_ident())
        return fresh_repodata

    monkeypatch.setattr(PrestoSolveRequest, "repodata_snapshot", capture_metadata)

    probe = await SolverResultService(ResultCache(max_size=8)).inspect(solver_request)

    assert probe.current == fresh_repodata
    assert metadata_threads
    assert metadata_threads != [caller_thread]


@pytest.mark.anyio
async def test_warm_service_does_not_use_the_default_thread_limiter(
    monkeypatch,
    solver_request,
    fresh_repodata,
    successful_result,
):
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata,
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
        solve_final_state=lambda *_: successful_result.outcome,
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
                    result = await service.resolve(solver_request, worker, 30)
            finally:
                release.set()
    finally:
        default_limiter.total_tokens = original_tokens

    assert result.disposition == "published"


@pytest.mark.anyio
async def test_persistent_store_timeout_is_bounded_and_backed_off(
    monkeypatch,
    solver_request,
    fresh_repodata,
    successful_result,
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
            return successful_result.outcome

    monkeypatch.setattr(app_module, "RESULT_CACHE_STORE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(app_module, "SOLVER_CACHE_WARM_INITIAL_DELAY_S", 0)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata,
    )
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
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
    warmer.create_worker = lambda: worker

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
    assert cache.entries == {}
    assert warm_candidates.entries[fingerprint].consecutive_transient_failures == 1
    assert warmer.stats.rejected_publications == 1


@pytest.mark.anyio
async def test_warm_cycle_with_no_candidates_does_not_create_worker(
    create_warmer,
):
    service = RecordingService([])
    warmer = create_warmer(SolverWarmCandidates(max_size=32), service)

    def fail_create():
        pytest.fail("empty cycle created a worker")

    warmer.create_worker = fail_create

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
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    for name in ("a", "b", "c"):
        request = msgspec.structs.replace(solver_request, specs_to_add=[name])
        record_candidate_request(warm_candidates, request)
    service = RecordingService(
        [SolverServiceProbe(cached=True, current=fresh_repodata)] * expected,
        cache_size=cache_size,
        persistent=persistent,
    )
    warmer = create_warmer(warm_candidates, service, batch_size=batch_size)

    await warmer.cycle()

    assert len(service.inspect_calls) == expected
    assert warmer.stats.already_current == expected


@pytest.mark.anyio
async def test_fresh_cache_hit_does_not_create_worker(
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=True, current=fresh_repodata)]
    )
    warmer = create_warmer(warm_candidates, service)

    def fail_create():
        pytest.fail("fresh cache hit created a worker")

    warmer.create_worker = fail_create

    await warmer.cycle()

    assert service.resolve_calls == []
    assert warmer.stats.already_current == 1


@pytest.mark.anyio
@pytest.mark.parametrize("stale", [False, True], ids=["missing", "stale"])
async def test_missing_or_stale_cache_entry_is_replayed(
    stale,
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    current = replace(fresh_repodata, stale=stale)
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=current)],
        [successful_result],
    )
    worker = RecordingWorker()
    warmer = create_warmer(warm_candidates, service, worker=worker)

    await warmer.cycle()

    assert service.resolve_calls == [
        (
            solver_request,
            worker,
            min(
                app_module.SOLVE_TIMEOUT_S,
                app_module.SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
            ),
        )
    ]
    assert worker.starts == 1
    assert worker.stops == 1
    assert warmer.active_worker is None
    assert warmer.stats.successful_refreshes == 1


@pytest.mark.anyio
async def test_warmer_uses_service_with_one_worker_reused_and_cleaned_up(
    monkeypatch,
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        record_candidate_request(warm_candidates, request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)] * 2,
        [successful_result, successful_result],
    )
    worker = RecordingWorker()
    constructor_calls = []

    def create_worker(channels, platforms, **kwargs):
        constructor_calls.append((channels, platforms, kwargs))
        return worker

    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
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
                    app_module.SOLVE_TIMEOUT_S,
                    app_module.SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
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
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)]
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
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)]
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
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        record_candidate_request(warm_candidates, request)
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))

    class ArrivingService(RecordingService):
        async def resolve(self, request, worker, timeout_s):
            result = await super().resolve(request, worker, timeout_s)
            await limiter.arrive().acquire()
            limiter.limiter.release()
            return result

    service = ArrivingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)] * 2,
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
async def test_foreground_arrival_during_cache_inspection_stops_cycle(
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        record_candidate_request(warm_candidates, request)
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))

    class ArrivingInspectionService(RecordingService):
        async def inspect(self, request):
            probe = await super().inspect(request)
            await limiter.arrive().acquire()
            limiter.limiter.release()
            return probe

    service = ArrivingInspectionService(
        [SolverServiceProbe(cached=True, current=fresh_repodata)] * 2
    )
    warmer = create_warmer(warm_candidates, service, limiter=limiter)

    await warmer.cycle()

    assert len(service.inspect_calls) == 1
    assert warmer.stats.foreground_skips == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "inspection",
    [
        pytest.param("timeout", id="timeout"),
        pytest.param("metadata", id="metadata-unavailable"),
        pytest.param("persistence", id="persistence-failed"),
        pytest.param("local", id="local-source"),
        pytest.param("deterministic", id="deterministic-marker"),
    ],
)
async def test_foreground_arrival_during_inspection_outcomes_stops_cycle(
    inspection,
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("a", "b")
    ]
    warm_candidates = SolverWarmCandidates(max_size=32)
    for request in requests:
        fingerprint = record_candidate_request(warm_candidates, request)
        if inspection == "deterministic":
            warm_candidates.mark_deterministic_failure(fingerprint, fresh_repodata)
    limiter = ForegroundCapacity(anyio.CapacityLimiter(1))
    current = (
        RepodataSnapshot(
            (("file:///srv/channel/linux-64", "repodata.json", 10, 1),),
            False,
        )
        if inspection == "local"
        else fresh_repodata
    )

    class ArrivingInspectionService(RecordingService):
        async def inspect(self, request):
            self.inspect_calls.append(request)
            await limiter.arrive().acquire()
            limiter.limiter.release()
            if inspection == "timeout":
                raise TimeoutError
            return SolverServiceProbe(
                cached=False,
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


@pytest.mark.anyio
async def test_cycle_budget_stops_before_next_candidate(
    monkeypatch,
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    for name in ("a", "b"):
        record_candidate_request(
            warm_candidates,
            msgspec.structs.replace(solver_request, specs_to_add=[name]),
        )
    service = RecordingService(
        [SolverServiceProbe(cached=True, current=fresh_repodata)] * 2
    )
    clock = iter(
        [
            0,
            0,
            0,
            app_module.SOLVER_CACHE_WARM_CYCLE_BUDGET_S + 1,
        ]
    )
    monkeypatch.setattr(
        app_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock)),
    )
    warmer = create_warmer(warm_candidates, service)

    await warmer.cycle()

    assert len(service.inspect_calls) == 1


@pytest.mark.anyio
async def test_cycle_does_not_start_worker_after_inspection_uses_budget(
    monkeypatch,
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    clock = {"now": 0.0}

    class SlowInspectionService(RecordingService):
        async def inspect(self, request):
            probe = await super().inspect(request)
            clock["now"] = app_module.SOLVER_CACHE_WARM_CYCLE_BUDGET_S + 1
            return probe

    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    service = SlowInspectionService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)]
    )
    monkeypatch.setattr(
        app_module,
        "time",
        SimpleNamespace(monotonic=lambda: clock["now"]),
    )
    warmer = create_warmer(warm_candidates, service)

    def fail_create():
        pytest.fail("worker started after the cycle budget")

    warmer.create_worker = fail_create

    await warmer.cycle()

    assert service.inspect_calls == [solver_request]
    assert service.resolve_calls == []


@pytest.mark.anyio
async def test_matching_deterministic_failure_is_suppressed_while_fresh(
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    warm_candidates.mark_deterministic_failure(fingerprint, fresh_repodata)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)]
    )
    warmer = create_warmer(warm_candidates, service)

    await warmer.cycle()

    assert service.resolve_calls == []
    assert warmer.active_worker is None


@pytest.mark.anyio
@pytest.mark.parametrize("current_stale", [True, False], ids=["stale", "changed"])
async def test_deterministic_failure_is_retried_for_stale_or_changed_repodata(
    current_stale,
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    warm_candidates.mark_deterministic_failure(fingerprint, fresh_repodata)
    current = (
        replace(fresh_repodata, stale=True)
        if current_stale
        else RepodataSnapshot(
            (("https://conda.example/linux-64", "repodata.json", 11, 2),),
            False,
        )
    )
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=current)],
        [successful_result],
    )
    warmer = create_warmer(
        warm_candidates,
        service,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    assert len(service.resolve_calls) == 1


@pytest.mark.anyio
async def test_current_deterministic_solver_error_sets_snapshot_marker(
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    error = PrestoSolveError(kind="unsatisfiable", message="conflict")
    outcome = PrestoSolveOutcome(
        result=error,
        metadata_before=fresh_repodata,
        metadata_used=fresh_repodata,
    )
    result = SolverServiceResult(
        result=error,
        disposition="solver-error",
        metadata_used=fresh_repodata,
        outcome=outcome,
    )
    service = RecordingService(
        [
            SolverServiceProbe(cached=False, current=fresh_repodata),
            SolverServiceProbe(cached=False, current=fresh_repodata),
        ],
        [result],
    )
    warmer = create_warmer(
        warm_candidates,
        service,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    entry = warm_candidates.entries[fingerprint]
    assert entry.failed_repodata_records == fresh_repodata.records
    assert entry.retry_at == 0
    assert warmer.stats.failures == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure_kind", ["transient", "deterministic"])
async def test_new_foreground_success_wins_over_stale_warm_failure(
    failure_kind,
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    error = PrestoSolveError(kind="unsatisfiable", message="conflict")
    outcome = PrestoSolveOutcome(
        result=error,
        metadata_before=fresh_repodata,
        metadata_used=fresh_repodata,
    )
    deterministic_result = SolverServiceResult(
        result=error,
        disposition="solver-error",
        metadata_used=fresh_repodata,
        outcome=outcome,
    )

    class ConcurrentForegroundService(RecordingService):
        async def resolve(self, request, worker, timeout_s):
            self.resolve_calls.append((request, worker, timeout_s))
            warm_candidates.record(request)
            if failure_kind == "transient":
                raise RuntimeError("worker failed")
            return deterministic_result

    probes = [SolverServiceProbe(cached=False, current=fresh_repodata)]
    if failure_kind == "deterministic":
        probes.append(SolverServiceProbe(cached=False, current=fresh_repodata))
    service = ConcurrentForegroundService(probes)
    warmer = create_warmer(
        warm_candidates,
        service,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    entry = warm_candidates.entries[fingerprint]
    assert entry.request_count == 3
    assert entry.failed_repodata_records is None
    assert entry.retry_at == 0
    assert entry.consecutive_transient_failures == 0


@pytest.mark.anyio
async def test_transient_failure_uses_exponential_backoff(
    monkeypatch,
    solver_request,
    record_candidate_request,
    create_warmer,
):
    now = 1_000_000_100
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    service = RecordingService([])
    warmer = create_warmer(warm_candidates, service, interval_s=10)
    monkeypatch.setattr(
        app_module,
        "time",
        SimpleNamespace(time=lambda: now),
    )

    entry = warm_candidates.candidate(fingerprint, now=now)
    assert entry is not None
    await warmer.defer(entry)

    assert warm_candidates.entries[fingerprint].retry_at == now + 10
    assert warm_candidates.entries[fingerprint].consecutive_transient_failures == 1

    entry = msgspec.structs.replace(warm_candidates.entries[fingerprint])
    await warmer.defer(entry)

    assert warm_candidates.entries[fingerprint].retry_at == now + 20
    assert warm_candidates.entries[fingerprint].consecutive_transient_failures == 2


@pytest.mark.anyio
async def test_metadata_failure_defers_candidate_without_starting_worker(
    monkeypatch,
    solver_request,
    record_candidate_request,
    create_warmer,
):
    now = 1_000_000_100
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    service = RecordingService([SolverServiceProbe(cached=False, current=None)])
    warmer = create_warmer(warm_candidates, service, interval_s=10)
    monkeypatch.setattr(
        app_module,
        "time",
        SimpleNamespace(monotonic=lambda: 0, time=lambda: now),
    )

    await warmer.cycle()

    entry = warm_candidates.entries[fingerprint]
    assert entry.retry_at == now + 10
    assert entry.consecutive_transient_failures == 1
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
async def test_worker_timeout_or_death_defers_and_stops_cycle(
    stage,
    error,
    counter,
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    worker = RecordingWorker(
        start_error=error if stage == "start" else None,
        ready=stage != "solve" or not isinstance(error, RuntimeError),
    )
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)],
        [error] if stage == "solve" else [],
    )
    warmer = create_warmer(warm_candidates, service, worker=worker)

    await warmer.cycle()

    assert getattr(warmer.stats, counter) == 1
    assert warm_candidates.entries[fingerprint].consecutive_transient_failures == 1
    assert worker.stops == 1
    assert warmer.active_worker is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "disposition",
    ["publication-rejected", "not-retained"],
)
async def test_rejected_publication_defers_candidate(
    disposition,
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
    result = replace(successful_result, disposition=disposition)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)],
        [result],
    )
    warmer = create_warmer(
        warm_candidates,
        service,
        worker=RecordingWorker(),
    )

    await warmer.cycle()

    assert warmer.stats.rejected_publications == 1
    assert warm_candidates.entries[fingerprint].consecutive_transient_failures == 1


@pytest.mark.anyio
async def test_file_source_is_discarded_without_replay(
    solver_request,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    fingerprint = record_candidate_request(warm_candidates, solver_request)
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
async def test_scheduler_stops_via_signal_without_sleeping(
    solver_request,
    create_warmer,
):
    warmer = create_warmer(
        SolverWarmCandidates(max_size=32),
        RecordingService([]),
        interval_s=123,
    )
    stop = anyio.Event()
    delays = []
    cycles = []

    async def wait_until_cycle(event, delay_s):
        delays.append(delay_s)
        if len(delays) == 2:
            event.set()
            return True
        return False

    async def cycle(_stop):
        cycles.append(solver_request)

    warmer.wait_until_cycle = wait_until_cycle
    warmer.cycle = cycle

    await warmer.run(stop)

    assert delays == [
        SOLVER_CACHE_WARM_INITIAL_DELAY_S,
        pytest.approx(123),
    ]
    assert cycles == [solver_request]


@pytest.mark.anyio
async def test_scheduler_contains_cycle_failures_without_sleeping(create_warmer):
    warmer = create_warmer(
        SolverWarmCandidates(max_size=32),
        RecordingService([]),
    )
    stop = anyio.Event()
    waits = 0

    async def wait_until_cycle(event, _delay_s):
        nonlocal waits
        waits += 1
        if waits == 2:
            event.set()
            return True
        return False

    async def fail_cycle(_stop):
        raise RuntimeError("cycle failed")

    warmer.wait_until_cycle = wait_until_cycle
    warmer.cycle = fail_cycle

    await warmer.run(stop)

    assert warmer.stats.failures == 1


@pytest.mark.anyio
async def test_warmer_lifespan_waits_for_cleanup_before_checkpoint(monkeypatch):
    events = []
    started = anyio.Event()
    finished = anyio.Event()
    foreground_worker = SimpleNamespace(
        ready=True,
        running=True,
        start=lambda: events.append("foreground-worker-start"),
        stop=lambda: events.append("foreground-worker-stop"),
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

    class Warmer:
        def __init__(self, **_kwargs):
            pass

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
    monkeypatch.setattr(app_module, "SOLVER_ENDPOINT", True)
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
async def test_broker_cycle_keeps_foreground_solver_ready(
    monkeypatch,
    solver_request,
    fresh_repodata,
    successful_result,
):
    service = next(conda_broker_services())
    assert service.process is not None
    assert service.process.env["CONDA_PRESTO_SOLVER_ENDPOINT"] == "1"
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

        def stop(self):
            self.ready = False
            self.running = False
            self.stops += 1

        def recover_if_stopped(self):
            return None

        def solve_final_state(self, *_):
            self.solve_calls += 1
            return successful_result.outcome

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
        "SOLVER_ENDPOINT": True,
    }.items():
        monkeypatch.setattr(app_module, name, value)
    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
    monkeypatch.setattr(app_module, "shutdown_process_pool", lambda: None)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: fresh_repodata,
    )
    app = Litestar(route_handlers=[health, solver_v1])

    async with solver_resources_lifespan(app):
        async with solver_cache_refresher_lifespan(app):
            foreground_worker = workers[0]
            app.state.solver_warm_candidates.record(solver_request)
            app.state.solver_warm_candidates.record(solver_request)

            await app.state.solver_cache_refresher.cycle()

            request = SimpleNamespace(
                app=app,
                client=SimpleNamespace(host="127.0.0.1"),
            )
            response = await solver_v1.fn(request, solver_request)
            readiness = await health.fn(request)

            warm_worker = workers[1]
            assert response.content == successful_result.result
            assert readiness == {"status": "ok"}
            assert foreground_worker.ready
            assert foreground_worker.solve_calls == 0
            assert warm_worker.solve_calls == 1
            assert warm_worker.stops == 1

    assert workers[0].stops == 1


@pytest.mark.anyio
async def test_cycle_cancellation_still_stops_dedicated_worker(
    solver_request,
    fresh_repodata,
    record_candidate_request,
    create_warmer,
):
    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    resolving = anyio.Event()

    class CancelledService(RecordingService):
        async def resolve(self, request, worker, timeout_s):
            resolving.set()
            await anyio.sleep_forever()

    service = CancelledService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)]
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
    solver_request,
    fresh_repodata,
    successful_result,
    record_candidate_request,
    create_warmer,
):
    class UnstoppableWorker(RecordingWorker):
        def stop(self):
            super().stop()
            return False

    warm_candidates = SolverWarmCandidates(max_size=32)
    record_candidate_request(warm_candidates, solver_request)
    service = RecordingService(
        [SolverServiceProbe(cached=False, current=fresh_repodata)],
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
    solver_request,
    record_candidate_request,
    create_warmer,
):
    secret_request = msgspec.structs.replace(
        solver_request,
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

    with caplog.at_level(logging.INFO, logger="conda_presto.app"):
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
