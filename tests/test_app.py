"""Tests for conda_presto.app (Litestar endpoints)."""

from __future__ import annotations

import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import anyio
import msgspec
import pytest
import yaml
from conda.exceptions import PackagesNotFoundError
from conda.models.environment import Environment
from httpx import ASGITransport, AsyncClient
from litestar import Litestar
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import JsonRenderPlugin
from litestar.stores.memory import MemoryStore

import conda_presto.app as app_module
import conda_presto.cache as cache_module
import conda_presto.inputs as inputs_module
from conda_presto.app import (
    build_cors_config,
    diff_post,
    explain_post,
    formats,
    health,
    parse,
    platforms,
    preflight_post,
    repair_post,
    resolve_get,
    resolve_post,
    result_get,
    solver_resources_lifespan,
    solver_v1,
    transcode_post,
    version,
)
from conda_presto.cache import ResultCache
from conda_presto.inputs import ParsedInputFile
from conda_presto.resolve import RepodataSnapshot, ResolvedPackage, SolveResult
from conda_presto.solver import (
    PrestoSolveError,
    PrestoSolveOutcome,
    PrestoSolverClient,
    PrestoSolveRequest,
    PrestoSolveResponse,
)
from conda_presto.storage import StoreOperationCoordinator
from conda_presto.warm_candidates import SolverWarmCandidates


@pytest.fixture()
def test_app():
    app = Litestar(
        route_handlers=[
            resolve_get,
            resolve_post,
            preflight_post,
            repair_post,
            diff_post,
            explain_post,
            transcode_post,
            result_get,
            formats,
            platforms,
            version,
            parse,
            health,
            solver_v1,
        ],
        openapi_config=OpenAPIConfig(
            title="conda-presto",
            version="test",
            path="/",
            render_plugins=[JsonRenderPlugin()],
        ),
        request_max_body_size=1_024 * 1_024,
    )
    app.state.solver_limiter = None
    app.state.result_cache = ResultCache(max_size=256)
    return app


@pytest.fixture()
async def client(test_app):
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def enabled_solver_endpoint(monkeypatch):
    monkeypatch.setenv("CONDA_BROKER_SERVICE_NAME", PrestoSolverClient.service_name)
    monkeypatch.setenv("CONDA_PRESTO_URL", "http://test")


@pytest.fixture()
def fake_solve_process(monkeypatch):
    def create(*, result=("ok", []), timed_out=False, alive=()):
        calls = []
        alive_states = iter(alive)

        def receive():
            if isinstance(result, Exception):
                raise result
            return result

        receiver = SimpleNamespace(
            poll=lambda _: not timed_out,
            recv=receive,
            close=lambda: calls.append("receiver.close"),
        )
        sender = SimpleNamespace(close=lambda: calls.append("sender.close"))
        process = SimpleNamespace(
            exitcode=1,
            start=lambda: calls.append("start"),
            is_alive=lambda: next(alive_states, False),
            terminate=lambda: calls.append("terminate"),
            kill=lambda: calls.append("kill"),
            join=lambda timeout=None: calls.append(f"join:{timeout}"),
        )
        context = SimpleNamespace(
            Pipe=lambda **_: (receiver, sender),
            Process=lambda **_: process,
        )
        monkeypatch.setattr(
            app_module.multiprocessing, "get_context", lambda _: context
        )
        return calls

    return create


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_health_is_unavailable_when_persistent_worker_stops(client, test_app):
    recoveries = []
    test_app.state.solve_worker = SimpleNamespace(
        ready=False,
        recover_if_stopped=lambda: recoveries.append("recover"),
    )

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert recoveries == ["recover"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "service_name",
    [
        pytest.param(None, id="normal-or-docker-server"),
        pytest.param("other.server", id="different-broker-service"),
    ],
)
async def test_solver_v1_requires_broker_service_identity(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    service_name,
):
    if service_name is None:
        monkeypatch.delenv("CONDA_BROKER_SERVICE_NAME", raising=False)
    else:
        monkeypatch.setenv("CONDA_BROKER_SERVICE_NAME", service_name)
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: pytest.fail("solver must remain unavailable")
    )

    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_solver_v1_rejects_before_parsing_the_request_body(client):
    response = await client.post(
        "/solver/v1",
        content=b"{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 404


@pytest.mark.anyio
async def test_solver_v1_rejects_unknown_request_fields(
    client,
    test_app,
    presto_solver_request,
    enabled_solver_endpoint,
):
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: pytest.fail("invalid request reached the solver")
    )
    body = msgspec.to_builtins(presto_solver_request)
    body["future_solver_setting"] = True

    response = await client.post("/solver/v1", json=body)

    assert response.status_code == 400
    assert response.json()["extra"] == [
        {
            "message": "Object contains unknown field `future_solver_setting`",
            "key": "data",
            "source": "body",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param(
            {"content-type": "application/json", "origin": "https://evil.example"},
            id="browser-origin",
        ),
        pytest.param(
            {"content-type": "application/json", "host": "other.example"},
            id="wrong-host",
        ),
        pytest.param({}, id="missing-content-type"),
        pytest.param({"content-type": "text/plain"}, id="text"),
        pytest.param(
            {"content-type": "application/x-www-form-urlencoded"},
            id="form",
        ),
    ],
)
async def test_solver_v1_rejects_untrusted_transport_before_parsing(
    client,
    enabled_solver_endpoint,
    headers,
):
    response = await client.post("/solver/v1", content=b"{", headers=headers)

    assert response.status_code == 404


@pytest.mark.anyio
async def test_solver_v1_accepts_json_content_type_parameters(
    client,
    enabled_solver_endpoint,
):
    response = await client.post(
        "/solver/v1",
        content=b"{",
        headers={"content-type": "application/json; charset=utf-8"},
    )

    assert response.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("field", "values", "limit_name"),
    [
        pytest.param(
            "channels",
            [{}, {}],
            "MAX_SOLVER_CHANNELS",
            id="channels",
        ),
        pytest.param(
            "subdirs",
            ["linux-64", "linux-64"],
            "MAX_PLATFORMS",
            id="subdirs",
        ),
        pytest.param(
            "specs_to_add",
            ["a", "b"],
            "MAX_SOLVER_STATE_ITEMS",
            id="add-specs",
        ),
        pytest.param(
            "specs_to_remove",
            ["a", "b"],
            "MAX_SOLVER_STATE_ITEMS",
            id="remove-specs",
        ),
        pytest.param(
            "history",
            ["a", "b"],
            "MAX_SOLVER_STATE_ITEMS",
            id="history",
        ),
        pytest.param(
            "pinned",
            ["a", "b"],
            "MAX_SOLVER_STATE_ITEMS",
            id="pinned",
        ),
        pytest.param(
            "aggressive_updates",
            ["a", "b"],
            "MAX_SOLVER_STATE_ITEMS",
            id="aggressive-updates",
        ),
        pytest.param(
            "always_update",
            ["a", "b"],
            "MAX_SOLVER_STATE_ITEMS",
            id="always-update",
        ),
        pytest.param(
            "installed",
            [{}, {}],
            "MAX_SOLVER_STATE_ITEMS",
            id="installed",
        ),
        pytest.param(
            "virtual",
            [{}, {}],
            "MAX_SOLVER_STATE_ITEMS",
            id="virtual",
        ),
    ],
)
async def test_solver_v1_bounds_every_request_array(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    enabled_solver_endpoint,
    field,
    values,
    limit_name,
):
    monkeypatch.setattr(app_module, limit_name, 1)
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: pytest.fail("oversized request reached solver")
    )
    body = msgspec.to_builtins(presto_solver_request)
    body[field] = values

    response = await client.post("/solver/v1", json=body)

    assert response.status_code == 400


@pytest.mark.anyio
async def test_solver_v1_bounds_combined_state_entries(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    enabled_solver_endpoint,
):
    monkeypatch.setattr(app_module, "MAX_SOLVER_STATE_ITEMS", 1)
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: pytest.fail("oversized request reached solver")
    )
    body = msgspec.to_builtins(presto_solver_request)
    body["specs_to_remove"] = ["bzip2"]

    response = await client.post("/solver/v1", json=body)

    assert response.status_code == 400


@pytest.mark.anyio
async def test_solver_v1_does_not_apply_public_spec_cap_to_installed_state(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    enabled_solver_endpoint,
):
    monkeypatch.setattr(app_module, "MAX_SPECS", 1)
    test_app.state.solve_worker = SimpleNamespace()
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )

    async def cached_result(_service, _request):
        return SimpleNamespace(
            result=PrestoSolveResponse(records=[], neutered=[]),
            should_record_for_warming=False,
        )

    monkeypatch.setattr(cache_module.SolverResultService, "probe", cached_result)
    body = msgspec.to_builtins(presto_solver_request)
    body["installed"] = [{} for _ in range(2)]

    response = await client.post("/solver/v1", json=body)

    assert response.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("candidate_size", "expected_recorded"),
    [
        pytest.param(32, 2, id="recorded"),
        pytest.param(0, 0, id="recording-disabled"),
    ],
)
async def test_solver_v1_caches_successful_final_state(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
    candidate_size,
    expected_recorded,
):
    calls = []

    def solve(data, timeout):
        calls.append((data, timeout))
        return presto_solver_outcome(
            PrestoSolveResponse(records=[], neutered=[]),
            fresh_repodata_snapshot,
        )

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    test_app.state.solve_worker = SimpleNamespace(solve_final_state=solve)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    test_app.state.solver_warm_candidates = SolverWarmCandidates(
        max_size=candidate_size
    )
    test_app.state.solver_cache_refresher = SimpleNamespace(
        stats=SimpleNamespace(recorded_requests=0)
    )

    first = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )
    second = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.json() == {"records": [], "neutered": []}
    assert second.headers["cache-control"] == "no-store"
    assert "location" not in second.headers
    assert [data for data, _ in calls] == [presto_solver_request]
    assert calls[0][1] > time.monotonic()
    assert test_app.state.solver_limiter.generation == 1
    entries = test_app.state.solver_warm_candidates.entries.values()
    assert [entry.request_count for entry in entries] == (
        [expected_recorded] if expected_recorded else []
    )
    assert (
        test_app.state.solver_cache_refresher.stats.recorded_requests
        == expected_recorded
    )


@pytest.mark.anyio
async def test_solver_v1_cache_hit_bypasses_solver_capacity(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    test_app.state.result_cache.remember_memory(
        key,
        cache_module.StoredSolverResult(
            response=PrestoSolveResponse(records=[], neutered=[]),
            metadata_used=fresh_repodata_snapshot,
        ),
    )
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: pytest.fail("cache hit must not solve")
    )
    limiter = anyio.CapacityLimiter(1)
    capacity = app_module.ForegroundCapacity(limiter)
    test_app.state.solver_limiter = capacity

    await limiter.acquire()
    try:
        with anyio.fail_after(0.1):
            response = await client.post(
                "/solver/v1",
                content=msgspec.json.encode(presto_solver_request),
                headers={"content-type": "application/json"},
            )
    finally:
        limiter.release()

    assert response.status_code == 200
    assert response.json() == {"records": [], "neutered": []}
    assert capacity.generation == 0


@pytest.mark.anyio
async def test_solver_v1_cache_hit_times_out_after_snapshot_cleanup(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    started = threading.Event()
    release = threading.Event()
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    test_app.state.result_cache.remember_memory(
        key,
        cache_module.StoredSolverResult(
            response=PrestoSolveResponse(records=[], neutered=[]),
            metadata_used=fresh_repodata_snapshot,
        ),
    )

    def snapshot(_):
        started.set()
        release.wait()
        return fresh_repodata_snapshot

    monkeypatch.setattr(app_module, "SOLVE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(PrestoSolveRequest, "repodata_snapshot", snapshot)
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: pytest.fail("cache hit must not solve")
    )

    timer = threading.Timer(0.05, release.set)
    timer.start()
    before = time.monotonic()
    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )
    elapsed = time.monotonic() - before
    timer.join()

    assert started.is_set()
    assert response.status_code == 504
    assert elapsed >= 0.04


@pytest.mark.anyio
async def test_solver_v1_timeout_includes_capacity_queue(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    calls = []
    occupied = anyio.Event()
    release = anyio.Event()
    monkeypatch.setattr(app_module, "SOLVE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: calls.append("solve")
    )
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )

    async def occupy_limiter():
        async with test_app.state.solver_limiter.arrive():
            occupied.set()
            await release.wait()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(occupy_limiter)
        await occupied.wait()
        response = await client.post(
            "/solver/v1",
            content=msgspec.json.encode(presto_solver_request),
            headers={"content-type": "application/json"},
        )
        release.set()

    assert response.status_code == 504
    assert calls == []


@pytest.mark.anyio
async def test_solver_v1_timeout_does_not_wait_for_worker_cleanup(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    started = threading.Event()
    release = threading.Event()

    def solve(*_):
        started.set()
        release.wait(1)
        raise TimeoutError

    monkeypatch.setattr(app_module, "SOLVE_TIMEOUT_S", 0.02)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    test_app.state.solve_worker = SimpleNamespace(solve_final_state=solve)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )

    before = time.monotonic()
    try:
        response = await client.post(
            "/solver/v1",
            content=msgspec.json.encode(presto_solver_request),
            headers={"content-type": "application/json"},
        )
        elapsed = time.monotonic() - before
    finally:
        release.set()

    assert started.is_set()
    assert response.status_code == 504
    assert elapsed < 0.5


@pytest.mark.anyio
async def test_solver_v1_publication_times_out_after_snapshot_cleanup(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    started = threading.Event()
    release = threading.Event()

    def snapshot(_):
        started.set()
        release.wait()
        return fresh_repodata_snapshot

    monkeypatch.setattr(app_module, "SOLVE_TIMEOUT_S", 0.01)
    monkeypatch.setattr(PrestoSolveRequest, "repodata_snapshot", snapshot)
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: presto_solver_outcome(
            PrestoSolveResponse(records=[], neutered=[]),
            fresh_repodata_snapshot,
        )
    )
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )

    timer = threading.Timer(0.05, release.set)
    timer.start()
    before = time.monotonic()
    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )
    elapsed = time.monotonic() - before
    timer.join()

    assert started.is_set()
    assert response.status_code == 504
    assert elapsed >= 0.04


@pytest.mark.anyio
async def test_solver_v1_coalesces_concurrent_cache_misses(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    calls = []
    responses = []
    solve_started = threading.Event()
    release_solve = threading.Event()

    def solve(data, timeout):
        calls.append((data, timeout))
        solve_started.set()
        release_solve.wait()
        return presto_solver_outcome(
            PrestoSolveResponse(records=[], neutered=[]),
            fresh_repodata_snapshot,
        )

    async def post_solve():
        responses.append(
            await client.post(
                "/solver/v1",
                content=msgspec.json.encode(presto_solver_request),
                headers={"content-type": "application/json"},
            )
        )

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    test_app.state.solve_worker = SimpleNamespace(solve_final_state=solve)
    capacity = app_module.ForegroundCapacity(anyio.CapacityLimiter(1))
    test_app.state.solver_limiter = capacity
    test_app.state.solver_warm_candidates = SolverWarmCandidates(max_size=32)
    test_app.state.solver_cache_refresher = SimpleNamespace(
        stats=SimpleNamespace(recorded_requests=0)
    )

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(post_solve)
            with anyio.fail_after(1):
                while not solve_started.is_set():
                    await anyio.lowlevel.checkpoint()
            tasks.start_soon(post_solve)
            with anyio.fail_after(1):
                while capacity.limiter.statistics().tasks_waiting == 0:
                    await anyio.lowlevel.checkpoint()
            statistics = capacity.limiter.statistics()
            assert statistics.borrowed_tokens == 1
            assert statistics.tasks_waiting == 1
            release_solve.set()
    finally:
        release_solve.set()

    assert [response.status_code for response in responses] == [200, 200]
    assert [data for data, _ in calls] == [presto_solver_request]
    entry = next(iter(test_app.state.solver_warm_candidates.entries.values()))
    assert entry.request_count == 2
    assert test_app.state.solver_cache_refresher.stats.recorded_requests == 2


@pytest.mark.anyio
async def test_solver_v1_returns_structured_conda_error(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    calls = []
    error = PrestoSolveError.from_exception(
        PackagesNotFoundError(["missing"], ["https://example.invalid"])
    )

    def solve(data, timeout):
        calls.append((data, timeout))
        return PrestoSolveOutcome(error, None, None)

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    test_app.state.solve_worker = SimpleNamespace(solve_final_state=solve)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    test_app.state.solver_warm_candidates = SolverWarmCandidates(max_size=32)

    responses = [
        await client.post(
            "/solver/v1",
            content=msgspec.json.encode(presto_solver_request),
            headers={"content-type": "application/json"},
        )
        for _ in range(2)
    ]

    assert [response.status_code for response in responses] == [422, 422]
    assert responses[0].json()["kind"] == "packages-not-found"
    assert responses[0].json()["packages"] == ["missing"]
    assert [data for data, _ in calls] == [presto_solver_request] * 2
    assert not test_app.state.solver_warm_candidates.entries


@pytest.mark.anyio
async def test_solver_v1_does_not_publish_after_repodata_changes(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    enabled_solver_endpoint,
):
    stale = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        True,
    )
    refreshed = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    stale_key = ResultCache.solver_key(presto_solver_request.cache_key())
    test_app.state.result_cache.remember_memory(
        stale_key,
        cache_module.StoredSolverResult(
            response=PrestoSolveResponse(records=[], neutered=[]),
            metadata_used=RepodataSnapshot(stale.records, False),
        ),
    )
    snapshots = iter([stale, refreshed])
    calls = []

    def solve(data, timeout):
        calls.append((data, timeout))
        return presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "zlib"}], neutered=[]),
            stale,
            refreshed,
        )

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: next(snapshots),
    )
    test_app.state.solve_worker = SimpleNamespace(solve_final_state=solve)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )

    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    refreshed_key = ResultCache.solver_key(presto_solver_request.cache_key())
    assert response.status_code == 200
    assert response.json()["records"] == [{"name": "zlib"}]
    assert [data for data, _ in calls] == [presto_solver_request]
    stored = test_app.state.result_cache.entries[refreshed_key]
    assert stored.response.records == []


@pytest.mark.anyio
async def test_solver_v1_does_not_cache_transient_shard_fallback(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    enabled_solver_endpoint,
):
    records = (
        (
            "https://conda.example/linux-64",
            "repodata_shards.msgpack.zst",
            10,
            1,
        ),
    )
    snapshots = iter(
        [
            RepodataSnapshot(records, True),
            RepodataSnapshot(records, False),
        ]
    )

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: next(snapshots),
    )
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: presto_solver_outcome(
            PrestoSolveResponse(
                records=[{"name": "zlib"}],
                neutered=[],
            ),
            RepodataSnapshot(records, True),
            RepodataSnapshot(records, False),
        )
    )
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    test_app.state.solver_warm_candidates = SolverWarmCandidates(max_size=32)

    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["records"] == [{"name": "zlib"}]
    assert not test_app.state.result_cache.entries
    assert not test_app.state.solver_warm_candidates.entries


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure_index", "expected_cached"),
    [
        pytest.param(0, True, id="pre-solve"),
        pytest.param(2, False, id="post-solve"),
    ],
)
async def test_solver_v1_returns_success_when_cache_snapshot_fails(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    failure_index,
    expected_cached,
    enabled_solver_endpoint,
):
    previous = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 5, 0),),
        False,
    )
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    test_app.state.result_cache.remember_memory(
        key,
        cache_module.StoredSolverResult(
            response=PrestoSolveResponse(records=[], neutered=[]),
            metadata_used=previous,
        ),
    )
    snapshots = iter(
        [
            RuntimeError("cache state unavailable")
            if index == failure_index
            else fresh_repodata_snapshot
            for index in range(3)
        ]
    )

    def snapshot(_):
        result = next(snapshots)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(PrestoSolveRequest, "repodata_snapshot", snapshot)
    test_app.state.solve_worker = SimpleNamespace(
        solve_final_state=lambda *_: presto_solver_outcome(
            PrestoSolveResponse(
                records=[{"name": "zlib"}],
                neutered=[],
            ),
            fresh_repodata_snapshot,
        )
    )
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )

    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["records"] == [{"name": "zlib"}]
    stored = test_app.state.result_cache.entries[key]
    assert stored.metadata_used == (
        fresh_repodata_snapshot if expected_cached else previous
    )


def test_build_cors_config_disabled_without_origins():
    assert build_cors_config([]) is None


def test_http_logging_excludes_sensitive_request_data():
    logging_config = app_module.middleware[0].kwargs["config"]

    assert logging_config.exclude == r"^/solver/v1$"
    assert logging_config.request_log_fields == ("path", "method", "content_type")
    assert logging_config.response_log_fields == ("status_code",)


def test_http_logging_disables_client_error_stack_traces():
    assert app_module.app.logging_config.disable_stack_trace == set(range(400, 500))


def test_build_cors_config_enabled_for_explicit_origins():
    cors = build_cors_config(["https://app.example.com"])
    assert cors is not None
    assert cors.allow_origins == ["https://app.example.com"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(
            ["linux-64", "osx-arm64"],
            id="multi",
            marks=pytest.mark.crossplatform,
        ),
    ],
)
async def test_resolve_post_specs(client, platforms):
    resp = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["zlib"],
            "platforms": platforms,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == len(platforms)
    for result, platform in zip(data, platforms):
        assert result["platform"] == platform
        assert result["error"] is None
        names = [p["name"] for p in result["packages"]]
        assert "zlib" in names
        for pkg in result["packages"]:
            assert pkg["sha256"], f"{pkg['name']} missing sha256"
            assert pkg["url"], f"{pkg['name']} missing url"


@pytest.mark.anyio
async def test_resolve_get_specs(client):
    resp = await client.get(
        "/resolve",
        params=[
            ("spec", "zlib"),
            ("channel", "conda-forge"),
            ("platform", "linux-64"),
        ],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["platform"] == "linux-64"
    assert data[0]["error"] is None
    names = [p["name"] for p in data[0]["packages"]]
    assert "zlib" in names


@pytest.mark.anyio
async def test_resolve_post_defaults(client):
    resp = await client.post(
        "/resolve",
        json={"specs": ["zlib"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["error"] is None


@pytest.mark.anyio
async def test_resolve_post_returns_content_addressed_location(client, monkeypatch):
    calls = 0

    def fake_solve(channels, specs, platforms, **kwargs):
        nonlocal calls
        calls += 1
        return [SolveResult(platform="linux-64", packages=[])]

    monkeypatch.setattr(app_module, "solve", fake_solve)

    first = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["zlib"],
            "platforms": ["linux-64"],
        },
    )
    second = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["zlib"],
            "platforms": ["linux-64"],
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == 1
    assert first.headers["location"].startswith("/r/")
    assert second.headers["location"] == first.headers["location"]
    assert first.headers["cache-control"] == cache_module.RESULT_RESPONSE_CACHE_CONTROL
    assert second.json() == [{"platform": "linux-64", "packages": [], "error": None}]


@pytest.mark.anyio
async def test_result_permalink_returns_stored_body_and_media_type(client, monkeypatch):
    monkeypatch.setattr(
        app_module,
        "solve",
        lambda channels, specs, platforms, **kwargs: [
            SolveResult(platform="linux-64", packages=[])
        ],
    )

    resolved = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["zlib"],
            "platforms": ["linux-64"],
        },
    )
    cached = await client.get(resolved.headers["location"])

    assert cached.status_code == 200
    assert cached.content == resolved.content
    assert cached.headers["content-type"].startswith("application/json")
    assert cached.headers["cache-control"] == cache_module.PERMALINK_CACHE_CONTROL


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("channel", "spec"),
    [
        pytest.param(
            "https://user:password@example.test/channel",
            "zlib",
            id="channel-userinfo",
        ),
        pytest.param(
            "https://example.test/t/token/channel",
            "zlib",
            id="channel-token",
        ),
        pytest.param(
            "https://example.test/%74/encoded/channel",
            "zlib",
            id="channel-encoded-token-segment",
        ),
        pytest.param(
            "https://example.test/t%2Fencoded/channel",
            "zlib",
            id="channel-encoded-token-path",
        ),
        pytest.param(
            "https://example.test/channel?signature=secret",
            "zlib",
            id="channel-query",
        ),
        pytest.param(
            "https://example.test/channel#secret",
            "zlib",
            id="channel-fragment",
        ),
        pytest.param(
            "conda-forge",
            "https://user:password@example.test/channel::zlib",
            id="spec-channel-url",
        ),
        pytest.param(
            "conda-forge",
            "zlib[url=https://user:password@example.test/pkg.conda]",
            id="spec-url-field",
        ),
    ],
)
async def test_resolve_does_not_retain_credentialed_request_values(
    client,
    test_app,
    monkeypatch,
    fresh_repodata_snapshot,
    channel,
    spec,
):
    calls = []
    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", ["*"])
    monkeypatch.setattr(
        app_module.RepodataSnapshot,
        "capture",
        staticmethod(lambda *_args, **_kwargs: fresh_repodata_snapshot),
    )
    monkeypatch.setattr(
        app_module,
        "solve",
        lambda *_args, **_kwargs: (
            calls.append("solve") or [SolveResult(platform="linux-64", packages=[])]
        ),
    )
    body = {
        "channels": [channel],
        "specs": [spec],
        "platforms": ["linux-64"],
    }

    first = await client.post("/resolve", json=body)
    second = await client.post("/resolve", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls == ["solve", "solve"]
    assert first.headers["cache-control"] == cache_module.RESULT_RESPONSE_CACHE_CONTROL
    assert "location" not in first.headers
    assert test_app.state.result_cache.entries == {}


@pytest.mark.anyio
async def test_resolve_validates_exporter_before_cache_lookup(
    client,
    test_app,
    monkeypatch,
):
    monkeypatch.setattr(
        test_app.state.result_cache,
        "capture_state",
        lambda *_: pytest.fail("unknown exporter reached cache lookup"),
    )

    response = await client.get("/resolve?spec=zlib&format=not-installed")

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_resolve_does_not_retain_unversioned_exporter_results(
    client,
    test_app,
    monkeypatch,
    fresh_repodata_snapshot,
):
    calls = []
    monkeypatch.setattr(
        app_module.OutputFormat,
        "cache_identity",
        lambda _self: None,
    )
    monkeypatch.setattr(
        app_module.RepodataSnapshot,
        "capture",
        staticmethod(lambda *_args, **_kwargs: fresh_repodata_snapshot),
    )

    async def run(*_args, **_kwargs):
        calls.append("solve")
        return b"result", "text/plain"

    monkeypatch.setattr(app_module, "run_solve", run)

    first = await client.get("/resolve?spec=zlib&format=explicit")
    second = await client.get("/resolve?spec=zlib&format=explicit")

    assert first.status_code == second.status_code == 200
    assert calls == ["solve", "solve"]
    assert "location" not in first.headers
    assert test_app.state.result_cache.entries == {}


@pytest.mark.anyio
async def test_result_permalink_missing_returns_404(client):
    resp = await client.get("/r/not-in-cache")

    assert resp.status_code == 404
    assert resp.json()["error"] == "result not in cache; re-POST to recompute"


@pytest.mark.anyio
async def test_result_cache_evicts_oldest_result(client, test_app, monkeypatch):
    test_app.state.result_cache = ResultCache(max_size=1)
    monkeypatch.setattr(
        app_module,
        "solve",
        lambda channels, specs, platforms, **kwargs: [
            SolveResult(platform="linux-64", packages=[])
        ],
    )

    first = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["first"],
            "platforms": ["linux-64"],
        },
    )
    second = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["second"],
            "platforms": ["linux-64"],
        },
    )

    assert (await client.get(first.headers["location"])).status_code == 404
    assert (await client.get(second.headers["location"])).status_code == 200


@pytest.mark.anyio
async def test_result_cache_uses_litestar_store_layer(monkeypatch):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    app = Litestar(route_handlers=[resolve_post, result_get])
    app.state.solver_limiter = None
    app.state.result_cache = ResultCache(
        max_size=256,
        store_operations=store_operations,
    )
    calls = 0

    def fake_solve(channels, specs, platforms, **kwargs):
        nonlocal calls
        calls += 1
        return [SolveResult(platform="linux-64", packages=[])]

    monkeypatch.setattr(app_module, "solve", fake_solve)

    async with store_operations.lifespan():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as store_client:
            first = await store_client.post(
                "/resolve",
                json={
                    "channels": ["conda-forge"],
                    "specs": ["zlib"],
                    "platforms": ["linux-64"],
                },
            )
            app.state.result_cache = ResultCache(
                max_size=256,
                store_operations=store_operations,
            )
            second = await store_client.post(
                "/resolve",
                json={
                    "channels": ["conda-forge"],
                    "specs": ["zlib"],
                    "platforms": ["linux-64"],
                },
            )
            cached = await store_client.get(first.headers["location"])

    assert calls == 1
    assert second.headers["location"] == first.headers["location"]
    assert second.content == first.content
    assert cached.content == first.content


@pytest.mark.anyio
async def test_solver_v1_uses_private_persistent_cache(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    app = Litestar(route_handlers=[solver_v1, result_get])
    app.state.solver_limiter = app_module.ForegroundCapacity(anyio.CapacityLimiter(1))
    app.state.result_cache = ResultCache(
        max_size=256,
        store_operations=store_operations,
    )
    calls = []

    def solve(data, timeout):
        calls.append((data, timeout))
        return presto_solver_outcome(
            PrestoSolveResponse(records=[], neutered=[]),
            fresh_repodata_snapshot,
        )

    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    app.state.solve_worker = SimpleNamespace(solve_final_state=solve)

    async with store_operations.lifespan():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as store_client:
            first = await store_client.post(
                "/solver/v1",
                content=msgspec.json.encode(presto_solver_request),
                headers={"content-type": "application/json"},
            )
            app.state.result_cache = ResultCache(
                max_size=256,
                store_operations=store_operations,
            )
            second = await store_client.post(
                "/solver/v1",
                content=msgspec.json.encode(presto_solver_request),
                headers={"content-type": "application/json"},
            )
            public = await store_client.get(f"/r/{presto_solver_request.cache_key()}")
            digest = presto_solver_request.cache_key()
            private_entry = await store.get(ResultCache.solver_key(digest))
            public_entry = await store.get(ResultCache.resolve_key(digest))

    assert [data for data, _ in calls] == [presto_solver_request]
    assert first.content == second.content
    assert "location" not in second.headers
    assert public.status_code == 404
    assert private_entry is not None
    assert public_entry is None
    stored = msgspec.msgpack.decode(
        private_entry,
        type=cache_module.StoredSolverResult,
    )
    assert stored.response == PrestoSolveResponse(records=[], neutered=[])
    assert stored.metadata_used == fresh_repodata_snapshot


@pytest.mark.anyio
async def test_spec_order_canonicalization_reuses_cached_result(client, monkeypatch):
    calls = 0

    def fake_solve(channels, specs, platforms, **kwargs):
        nonlocal calls
        calls += 1
        return [SolveResult(platform="linux-64", packages=[])]

    monkeypatch.setattr(app_module, "solve", fake_solve)

    first = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["python", "zlib"],
            "platforms": ["linux-64"],
        },
    )
    second = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["zlib", "python"],
            "platforms": ["linux-64"],
        },
    )

    assert calls == 1
    assert second.headers["location"] == first.headers["location"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source", "expected_location"),
    [
        pytest.param("repodata.json", True, id="json"),
        pytest.param(
            "repodata_shards.msgpack.zst",
            False,
            id="transient-shard-fallback",
        ),
    ],
)
async def test_stale_repodata_bypasses_cached_result(
    client, test_app, monkeypatch, source, expected_location
):
    records = (("https://conda.example/linux-64", source, 10, 1),)
    stale = RepodataSnapshot(records, True)
    fresh = RepodataSnapshot(records, False)
    key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        stale,
    )
    test_app.state.result_cache.remember_memory(
        ResultCache.resolve_key(key),
        cache_module.StoredResult(b'[{"stale":true}]', "application/json"),
    )
    snapshots = iter([stale, fresh])
    monkeypatch.setattr(
        app_module.RepodataSnapshot,
        "capture",
        lambda *_: next(snapshots),
    )
    calls = []
    monkeypatch.setattr(
        app_module,
        "solve",
        lambda *_, **__: (
            calls.append("solve") or [SolveResult(platform="linux-64", packages=[])]
        ),
    )

    response = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "platforms": ["linux-64"]},
    )

    assert response.status_code == 200
    assert response.json() == [{"platform": "linux-64", "packages": [], "error": None}]
    assert calls == ["solve"]
    assert ("location" in response.headers) is expected_location


@pytest.mark.anyio
async def test_resolve_does_not_publish_across_repodata_marker_change(
    client,
    test_app,
    monkeypatch,
):
    before = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    after = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    snapshots = iter([before, after])
    monkeypatch.setattr(
        app_module.RepodataSnapshot,
        "capture",
        lambda *_args, **_kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        app_module,
        "solve",
        lambda *_args, **_kwargs: [SolveResult(platform="linux-64", packages=[])],
    )

    response = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "platforms": ["linux-64"]},
    )

    assert response.status_code == 200
    assert "location" not in response.headers
    assert test_app.state.result_cache.entries == {}


@pytest.mark.anyio
async def test_resolve_get_uses_default_channels(client, monkeypatch):
    captured = {}

    def capture(channels, specs, platforms, **kwargs):
        captured["channels"] = channels
        return [SolveResult(platform="linux-64", packages=[])]

    monkeypatch.setattr(app_module, "solve", capture)

    resp = await client.get(
        "/resolve",
        params=[("spec", "zlib"), ("platform", "linux-64")],
    )

    assert resp.status_code == 200
    assert captured["channels"] == app_module.DEFAULT_CHANNELS


@pytest.mark.anyio
async def test_resolve_post_empty_body_uses_query_params(client, monkeypatch):
    captured = {}

    def capture(channels, specs, platforms, **kwargs):
        captured["specs"] = specs
        return [SolveResult(platform="linux-64", packages=[])]

    monkeypatch.setattr(app_module, "solve", capture)

    resp = await client.post(
        "/resolve?spec=zlib&platform=linux-64",
        content=b"",
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 200
    assert captured["specs"] == ["zlib"]


@pytest.mark.anyio
async def test_version_omits_missing_optional_dependency(client, monkeypatch):
    def fake_version(package):
        if package == "conda-lockfiles":
            raise RuntimeError("missing")
        return "test"

    monkeypatch.setattr(app_module, "pkg_version", fake_version)

    resp = await client.get("/version")
    data = resp.json()

    assert resp.status_code == 200
    assert data["conda-presto"] == "test"
    assert data["conda-rattler-solver"] == "test"
    assert "conda-lockfiles" not in data


@pytest.mark.anyio
async def test_resolve_post_unsatisfiable(client):
    resp = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["nonexistent-package-xyz-zzzzzz"],
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["error"] is not None
    assert data[0]["packages"] == []
    assert "/Users/" not in data[0]["error"]


@pytest.mark.anyio
async def test_resolve_post_file(client):
    yml = (
        "name: test\n"
        "channels:\n"
        "  - conda-forge\n"
        "dependencies:\n"
        "  - python=3.12\n"
        "  - numpy\n"
    )
    resp = await client.post(
        "/resolve",
        json={
            "file": yml,
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    names = [p["name"] for p in data[0]["packages"]]
    assert "python" in names
    assert "numpy" in names


@pytest.mark.anyio
async def test_resolve_post_file_with_filename(client):
    yml = "name: test\nchannels:\n  - conda-forge\ndependencies:\n  - zlib\n"
    resp = await client.post(
        "/resolve",
        json={
            "file": yml,
            "filename": "environment.yaml",
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["error"] is None


@pytest.mark.anyio
async def test_resolve_post_merged_specs_and_file(client):
    yml = "name: test\nchannels:\n  - conda-forge\ndependencies:\n  - python=3.12\n"
    resp = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "file": yml,
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    names = [p["name"] for p in data[0]["packages"]]
    assert "python" in names
    assert "zlib" in names


@pytest.mark.anyio
async def test_resolve_post_body_overrides_query_params(client):
    resp = await client.post(
        "/resolve?channel=defaults&platform=osx-64",
        json={
            "specs": ["zlib"],
            "channels": ["conda-forge"],
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["platform"] == "linux-64"


@pytest.mark.anyio
async def test_resolve_post_invalid_json(client):
    resp = await client.post(
        "/resolve",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_resolve_post_not_object(client):
    resp = await client.post(
        "/resolve",
        json=["just", "a", "list"],
    )
    assert resp.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"channels": 123},
        {"specs": [None]},
        {"platforms": "linux-64"},
        {"specs": ["zlib"], "channels": ["ok"], "platforms": [1]},
    ],
)
async def test_resolve_post_invalid_types(client, body):
    resp = await client.post("/resolve", json=body)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_resolve_post_file_not_string(client):
    resp = await client.post(
        "/resolve",
        json={"file": 123},
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_resolve_post_bad_extension(client):
    resp = await client.post(
        "/resolve",
        json={
            "file": "some content",
            "filename": "malicious.exe",
        },
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_resolve_post_path_traversal(client):
    yml = "name: test\nchannels:\n  - conda-forge\ndependencies:\n  - zlib\n"
    resp = await client.post(
        "/resolve",
        json={
            "file": yml,
            "filename": "../../etc/environment.yml",
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["error"] is None


@pytest.mark.anyio
async def test_resolve_no_specs_or_file(client):
    resp = await client.post("/resolve", json={})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_resolve_get_no_specs(client):
    resp = await client.get("/resolve")
    assert resp.status_code == 400
    assert "Provide specs or file" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_body_too_large(client):
    resp = await client.post(
        "/resolve",
        content=b"x" * (1_024 * 1_024 + 1),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


@pytest.mark.anyio
async def test_resolve_internal_error(client, monkeypatch):
    monkeypatch.setattr(
        "conda_presto.app.solve",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    resp = await client.post(
        "/resolve",
        json={
            "channels": ["conda-forge"],
            "specs": ["zlib"],
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 500
    assert resp.json()["error"] == "Internal solver error"


@pytest.mark.anyio
async def test_resolve_generic_error_does_not_leak_paths(client, monkeypatch):
    def raise_with_path(*a, **kw):
        raise KeyError("/Users/secret/very/private/path")

    monkeypatch.setattr("conda_presto.app.solve", raise_with_path)
    resp = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "platforms": ["linux-64"]},
    )
    assert resp.status_code == 500
    assert "/Users/" not in resp.text
    assert "private" not in resp.text
    assert resp.json()["error"] == "Internal solver error"


@pytest.mark.anyio
async def test_resolve_solve_timeout(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.SOLVE_TIMEOUT_S", 0.1)

    def slow_solve(*a, **kw):
        time.sleep(2)
        return []

    monkeypatch.setattr("conda_presto.app.solve", slow_solve)
    resp = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "platforms": ["linux-64"]},
    )
    assert resp.status_code == 504
    assert "timeout" in resp.json()["error"].lower()


@pytest.mark.anyio
async def test_resolve_uses_terminable_worker_when_limiter_present(
    test_app, monkeypatch
):
    captured = {}

    def fake_run_solve_in_process(
        channels, specs, platforms, format_name, deadline, captured_errors
    ):
        captured["args"] = (
            channels,
            specs,
            platforms,
            format_name,
            deadline,
            captured_errors,
        )
        return []

    async def fake_run_sync(func, *args, abandon_on_cancel, limiter):
        captured["limiter"] = limiter
        captured["abandon_on_cancel"] = abandon_on_cancel
        return func(*args)

    monkeypatch.setattr(app_module, "run_solve_in_process", fake_run_solve_in_process)
    monkeypatch.setattr(app_module.anyio.to_thread, "run_sync", fake_run_sync)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve",
            json={"specs": ["zlib"], "platforms": ["linux-64"]},
        )

    assert response.status_code == 200
    assert captured["limiter"] is test_app.state.solver_limiter.limiter
    assert captured["abandon_on_cancel"] is False
    channels, specs, platforms, format_name, deadline, captured_errors = captured[
        "args"
    ]
    assert channels == ["conda-forge"]
    assert specs == ["zlib"]
    assert platforms == ["linux-64"]
    assert format_name is None
    assert deadline > time.monotonic()
    assert captured_errors == (Exception,)


@pytest.mark.anyio
async def test_resolve_worker_deadline_includes_capacity_wait(
    client, test_app, monkeypatch
):
    remaining = []
    limiter = app_module.anyio.CapacityLimiter(1)
    occupied = app_module.anyio.Event()

    async def occupy_solver():
        async with limiter:
            occupied.set()
            while limiter.statistics().tasks_waiting == 0:
                await app_module.anyio.sleep(0)
            await app_module.anyio.sleep(0.1)

    def fake_run_solve_in_process(
        channels, specs, platforms, format_name, deadline, captured_errors
    ):
        remaining.append(deadline - time.monotonic())
        return []

    monkeypatch.setattr(app_module, "SOLVE_TIMEOUT_S", 0.5)
    monkeypatch.setattr(app_module, "run_solve_in_process", fake_run_solve_in_process)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(limiter)

    async with app_module.anyio.create_task_group() as task_group:
        task_group.start_soon(occupy_solver)
        await occupied.wait()
        response = await client.post(
            "/resolve",
            json={"specs": ["zlib"], "platforms": ["linux-64"]},
        )

    assert response.status_code == 200
    assert len(remaining) == 1
    assert 0 < remaining[0] < 0.45


@pytest.mark.anyio
async def test_resolve_uses_persistent_worker_when_configured(test_app):
    calls = []

    def solve(*args):
        calls.append(args)
        return []

    test_app.state.solve_worker = SimpleNamespace(running=True, solve=solve)
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve",
            json={"specs": ["zlib"], "platforms": ["linux-64"]},
        )

    assert response.status_code == 200
    assert len(calls) == 1
    channels, specs, platforms, format_name, deadline = calls[0]
    assert (channels, specs, platforms, format_name) == (
        ["conda-forge"],
        ["zlib"],
        ["linux-64"],
        None,
    )
    assert deadline > time.monotonic()


@pytest.mark.anyio
async def test_run_solve_passes_per_call_timeout(test_app):
    calls = []

    def solve(*args):
        calls.append(args)
        return []

    test_app.state.solve_worker = SimpleNamespace(solve=solve)

    started = time.monotonic()
    response = await app_module.run_solve(
        SimpleNamespace(app=test_app),
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        timeout_s=0.25,
    )

    assert response == (b"[]", "application/json")
    assert len(calls) == 1
    channels, specs, platforms, format_name, deadline = calls[0]
    assert (channels, specs, platforms, format_name) == (
        ["conda-forge"],
        ["zlib"],
        ["linux-64"],
        None,
    )
    assert started < deadline <= time.monotonic() + 0.25


@pytest.mark.anyio
async def test_run_solve_timeout_includes_persistent_worker_queue(test_app):
    calls = []
    occupied = anyio.Event()
    release = anyio.Event()
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    test_app.state.solve_worker = SimpleNamespace(
        solve=lambda *_: calls.append("solve") or []
    )

    async def occupy_limiter():
        async with test_app.state.solver_limiter.arrive():
            occupied.set()
            await release.wait()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(occupy_limiter)
        await occupied.wait()
        response = await app_module.run_solve(
            SimpleNamespace(app=test_app),
            ["zlib"],
            ["conda-forge"],
            ["linux-64"],
            timeout_s=0.05,
        )
        release.set()

    assert response.status_code == 504
    assert calls == []


def test_run_solve_in_process_returns_worker_result(fake_solve_process):
    calls = fake_solve_process()

    result = app_module.run_solve_in_process(
        ["conda-forge"],
        ["zlib"],
        ["linux-64"],
        None,
        time.monotonic() + 60,
    )

    assert result == []
    assert calls == ["start", "sender.close", "receiver.close", "join:5"]


def test_run_solve_in_process_executes_worker():
    result = app_module.run_solve_in_process(
        ["conda-forge"],
        ["zlib"],
        ["linux-64"],
        None,
        time.monotonic() + 60,
    )

    assert result[0].platform == "linux-64"
    assert any(package.name == "zlib" for package in result[0].packages)


def test_run_solve_in_process_kills_timed_out_worker(fake_solve_process):
    calls = fake_solve_process(timed_out=True, alive=(True, True))

    with pytest.raises(TimeoutError):
        app_module.run_solve_in_process(
            ["conda-forge"],
            ["zlib"],
            ["linux-64"],
            None,
            time.monotonic() + 60,
        )

    assert calls == [
        "start",
        "sender.close",
        "receiver.close",
        "terminate",
        "join:5.0",
        "kill",
        "join:5",
    ]


@pytest.mark.parametrize(
    ("result", "error_type"),
    [
        pytest.param(
            ("unknown-format", {"format_name": "toml", "available": ["yaml"]}),
            app_module.UnknownFormatError,
            id="unknown-format",
        ),
        pytest.param(("error", None), RuntimeError, id="worker-error"),
        pytest.param(EOFError(), RuntimeError, id="worker-exit"),
    ],
)
def test_run_solve_in_process_raises_worker_error(
    fake_solve_process, result, error_type
):
    fake_solve_process(result=result)

    with pytest.raises(error_type):
        app_module.run_solve_in_process(
            ["conda-forge"],
            ["zlib"],
            ["linux-64"],
            None,
            time.monotonic() + 60,
        )


def test_run_solve_in_process_rejects_expired_deadline():
    with pytest.raises(TimeoutError):
        app_module.run_solve_in_process(
            ["conda-forge"],
            ["zlib"],
            ["linux-64"],
            None,
            0,
        )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        pytest.param([], ("ok", []), id="success"),
        pytest.param(
            app_module.UnknownFormatError("toml", ["yaml"]),
            ("unknown-format", {"format_name": "toml", "available": ["yaml"]}),
            id="unknown-format",
        ),
        pytest.param(RuntimeError("failed"), ("error", None), id="error"),
    ],
)
def test_solve_process_entrypoint_sends_result(monkeypatch, result, expected):
    sent = []
    sender = SimpleNamespace(send=sent.append, close=lambda: sent.append("closed"))

    def fake_work(*_):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(app_module, "run_solve_work", fake_work)

    app_module.solve_process_entrypoint(
        sender, ["conda-forge"], ["zlib"], ["linux-64"], None
    )

    assert sent == [expected, "closed"]


@pytest.mark.anyio
async def test_resolve_rejects_too_many_platforms(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.MAX_PLATFORMS", 2)
    resp = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "platforms": ["linux-64", "osx-64", "osx-arm64"],
        },
    )
    assert resp.status_code == 400
    assert "Too many platforms" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_rejects_unsupported_platform(client):
    response = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "platforms": ["not-a-platform"]},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Unsupported platform(s): not-a-platform"


@pytest.mark.anyio
async def test_resolve_rejects_too_many_specs(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.MAX_SPECS", 2)
    resp = await client.post(
        "/resolve",
        json={
            "specs": ["a", "b", "c"],
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 400
    assert "Too many specs" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_rejects_too_many_channels(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.MAX_CHANNELS", 1)

    resp = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "channels": ["conda-forge", "bioconda"],
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 400
    assert "Too many channels" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_rejects_unlisted_channel(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.CHANNEL_ALLOWLIST", ["conda-forge"])

    resp = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "channels": ["https://example.invalid/private"],
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 400
    body = resp.json()
    assert "Unsupported channel" in body["error"]
    assert "allowed_channels" not in body
    assert "example.invalid" not in resp.text


@pytest.mark.anyio
async def test_resolve_rejects_invalid_channel_allowlist(client, monkeypatch):
    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", ["https://["])

    response = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "channels": ["conda-forge"]},
    )

    assert response.status_code == 500
    assert response.json() == {"error": "Invalid server channel configuration"}


@pytest.mark.anyio
async def test_resolve_rejects_malformed_channel(client):
    response = await client.post(
        "/resolve",
        json={"specs": ["zlib"], "channels": ["https://["]},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "Unsupported channel(s)"}


@pytest.mark.anyio
async def test_resolve_accepts_allowed_channel_url(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.CHANNEL_ALLOWLIST", ["conda-forge"])
    monkeypatch.setattr(
        "conda_presto.app.solve",
        lambda channels, specs, platforms, **kwargs: [],
    )

    resp = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "channels": ["https://conda.anaconda.org/conda-forge"],
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("allowlist", "channel"),
    [
        pytest.param(
            ["defaults"],
            "http://169.254.169.254/pkgs/main",
            id="defaults-spoof",
        ),
        pytest.param(
            ["conda-forge"],
            "http://conda.anaconda.org/conda-forge",
            id="scheme-downgrade",
        ),
        pytest.param(["*"], "file:///tmp/channel", id="wildcard-local-file"),
    ],
)
async def test_resolve_rejects_channel_allowlist_bypasses(
    client,
    monkeypatch,
    allowlist,
    channel,
):
    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", allowlist)

    response = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "channels": [channel],
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == 400
    assert response.json() == {"error": "Unsupported channel(s)"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("allowlist", "channel"),
    [
        pytest.param(["defaults"], "defaults", id="defaults-name"),
        pytest.param(
            ["defaults"],
            "https://repo.anaconda.com/pkgs/main",
            id="defaults-url",
        ),
        pytest.param(["conda-forge"], "conda-forge", id="named-channel"),
        pytest.param(
            ["*"],
            "https://packages.example.test/team",
            id="wildcard-https",
        ),
        pytest.param(
            ["*", "file:///tmp/channel"],
            "file:///tmp/channel",
            id="explicit-local-file",
        ),
    ],
)
async def test_resolve_accepts_resolved_channel_identities(
    client,
    monkeypatch,
    allowlist,
    channel,
):
    async def accepted(*_args, **_kwargs):
        return app_module.Response([])

    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", allowlist)
    monkeypatch.setattr(app_module, "run_cached_solve", accepted)

    response = await client.post(
        "/resolve",
        json={
            "specs": ["zlib"],
            "channels": [channel],
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_resolve_get_rejects_too_many_platforms(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.MAX_PLATFORMS", 1)
    resp = await client.get(
        "/resolve",
        params=[
            ("spec", "zlib"),
            ("platform", "linux-64"),
            ("platform", "osx-arm64"),
        ],
    )
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_resolve_post_omitted_fields_fall_through_to_query(client, monkeypatch):
    captured = {}

    def capture(channels, specs, platforms, **kwargs):
        captured["channels"] = channels
        captured["specs"] = specs
        captured["platforms"] = platforms
        return []

    monkeypatch.setattr("conda_presto.app.solve", capture)
    resp = await client.post(
        "/resolve?channel=conda-forge&platform=linux-64",
        json={"specs": ["zlib"]},
    )
    assert resp.status_code == 200
    assert captured["specs"] == ["zlib"]
    assert captured["channels"] == ["conda-forge"]
    assert captured["platforms"] == ["linux-64"]


@pytest.mark.anyio
async def test_resolve_post_empty_body_array_overrides_query(client, monkeypatch):
    captured = {}

    def capture(channels, specs, platforms, **kwargs):
        captured["channels"] = channels
        captured["specs"] = specs
        captured["platforms"] = platforms
        return []

    monkeypatch.setattr("conda_presto.app.solve", capture)
    resp = await client.post(
        "/resolve?platform=osx-arm64",
        json={"specs": ["zlib"], "platforms": []},
    )
    assert resp.status_code == 200
    # Empty array in body overrides query; handler passes None to trigger
    # the NATIVE_SUBDIR default inside solve().
    assert captured["platforms"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fmt, content_marker, content_type_prefix",
    [
        pytest.param("explicit", "@EXPLICIT", "text/plain", id="explicit"),
        pytest.param(
            "environment-yaml",
            "dependencies:",
            "application/yaml",
            id="yaml",
        ),
        pytest.param(
            "environment-json",
            '"dependencies"',
            "application/json",
            id="environment-json",
        ),
    ],
)
async def test_resolve_get_format_query_param(
    client, fmt, content_marker, content_type_prefix
):
    resp = await client.get(
        "/resolve",
        params=[
            ("spec", "zlib"),
            ("channel", "conda-forge"),
            ("platform", "linux-64"),
            ("format", fmt),
        ],
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(content_type_prefix)
    assert content_marker in resp.text


@pytest.mark.anyio
async def test_resolve_post_format_query_param(client):
    resp = await client.post(
        "/resolve?format=explicit",
        json={
            "specs": ["zlib"],
            "channels": ["conda-forge"],
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "@EXPLICIT" in resp.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content_type, filename_override, body",
    [
        pytest.param(
            "application/yaml",
            None,
            "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
            id="yaml",
        ),
        pytest.param(
            "application/x-yaml",
            None,
            "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
            id="x-yaml",
        ),
        pytest.param(
            "text/yaml",
            None,
            "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
            id="text-yaml",
        ),
        pytest.param(
            "application/yaml; charset=utf-8",
            None,
            "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
            id="with-charset",
        ),
        pytest.param(
            "application/yaml",
            "environment.yaml",
            "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
            id="filename-override",
        ),
    ],
)
async def test_resolve_post_raw_yaml_body(
    client, content_type, filename_override, body
):
    """Raw environment.yml body with Content-Type: application/yaml works
    without JSON wrapping — the one-liner in the README."""
    url = "/resolve?platform=linux-64"
    if filename_override:
        url += f"&filename={filename_override}"
    resp = await client.post(
        url,
        content=body,
        headers={"content-type": content_type},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["platform"] == "linux-64"
    assert data[0]["error"] is None
    names = [p["name"] for p in data[0]["packages"]]
    assert "zlib" in names


@pytest.mark.anyio
async def test_resolve_post_raw_body_pixi_lock_pipeline(client):
    """End-to-end raw-body pipeline: YAML in -> pixi.lock out."""
    body = "channels:\n  - conda-forge\ndependencies:\n  - zlib\n"
    resp = await client.post(
        "/resolve?platform=linux-64&format=pixi-lock-v6",
        content=body,
        headers={"content-type": "application/yaml"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    data = yaml.safe_load(resp.text)
    assert data["version"] == 6
    assert "linux-64" in data["environments"]["default"]["packages"]


@pytest.mark.anyio
async def test_preflight_post_reports_findings_without_solving(client, monkeypatch):
    async def fail_run_solve(*args, **kwargs):
        raise AssertionError("preflight must not solve")

    monkeypatch.setattr(app_module, "run_solve", fail_run_solve)
    resp = await client.post(
        "/preflight",
        json={
            "specs": ["numpy=1.26.4", "numpy=1.26.4"],
            "channels": [
                "conda-forge",
                "https://conda.anaconda.org/conda-forge",
            ],
        },
    )

    assert resp.status_code == 200
    assert {finding["code"] for finding in resp.json()["findings"]} == {
        "PIN001",
        "DUP001",
        "CHN002",
    }


@pytest.mark.anyio
async def test_preflight_post_returns_parse_errors_as_findings(client):
    resp = await client.post(
        "/preflight",
        content="dependencies: [",
        headers={"content-type": "application/yaml"},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["findings"][0]["code"] == "ENV001"


@pytest.mark.anyio
async def test_preflight_post_uses_channels_from_a_parsed_file(client):
    resp = await client.post(
        "/preflight",
        json={
            "file": "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
            "filename": "environment.yml",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.anyio
async def test_repair_post_returns_no_suggestions_for_a_feasible_request(
    client, monkeypatch
):
    calls = []

    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        calls.append((specs, channels, platforms, kwargs["timeout_s"]))
        return (
            msgspec.json.encode(
                [SolveResult(platform=platform, packages=[]) for platform in platforms]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post("/repair", json={"specs": ["scipy==1.5"]})

    assert response.status_code == 200
    assert response.json() == {
        "feasible": True,
        "diagnosis": None,
        "suggestions": [],
        "completion_reason": "feasible",
    }
    assert len(calls) == 1


@pytest.mark.anyio
async def test_repair_post_verifies_an_exact_pin_relaxation_on_every_platform(
    client, monkeypatch
):
    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        error = "Unsatisfiable environment" if specs == ["scipy==1.5"] else None
        return (
            msgspec.json.encode(
                [
                    SolveResult(platform=platform, packages=[], error=error)
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post(
        "/repair",
        json={
            "specs": ["scipy==1.5"],
            "channels": ["conda-forge"],
            "platforms": ["linux-64", "osx-arm64"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "feasible": False,
        "diagnosis": {
            "kind": "solver_conflict",
            "summary": "Unsatisfiable environment",
        },
        "suggestions": [
            {
                "changes": [
                    {
                        "from": "scipy==1.5",
                        "to": "scipy",
                        "strategy": "relax_exact_pin",
                    }
                ],
                "solve_attempts": 1,
                "platforms": ["linux-64", "osx-arm64"],
            }
        ],
        "completion_reason": "exhausted",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "spec",
    [
        "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.1-h4ab18f5_1.conda",
        "zlib==1.3.1[fn=zlib-1.3.1-h4ab18f5_1.conda]",
    ],
    ids=["url", "filename"],
)
async def test_repair_post_does_not_relax_package_artifacts(client, monkeypatch, spec):
    calls = []

    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        calls.append(specs)
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platform,
                        packages=[],
                        error="Unsatisfiable environment",
                    )
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post("/repair", json={"specs": [spec]})

    assert response.status_code == 200
    assert response.json()["suggestions"] == []
    assert response.json()["completion_reason"] == "exhausted"
    assert calls == [[spec]]


@pytest.mark.anyio
async def test_repair_post_relaxes_one_side_of_a_bounded_spec(client, monkeypatch):
    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        error = None if specs == ["conda-forge::scipy[version='>=1.5']"] else "no"
        return (
            msgspec.json.encode(
                [
                    SolveResult(platform=platform, packages=[], error=error)
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post(
        "/repair",
        json={
            "specs": ["conda-forge::scipy>=1.5,<2"],
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == 200
    suggestion = response.json()["suggestions"][0]
    assert suggestion["changes"] == [
        {
            "from": "conda-forge::scipy>=1.5,<2",
            "to": "conda-forge::scipy[version='>=1.5']",
            "strategy": "drop_upper_bound",
        }
    ]
    assert suggestion["platforms"] == ["linux-64"]


@pytest.mark.anyio
async def test_repair_post_keeps_verified_results_when_the_attempt_budget_ends(
    client, monkeypatch
):
    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        solved = specs == ["first", "second==1"]
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platform,
                        packages=[],
                        error=None if solved else "Unsatisfiable environment",
                    )
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post(
        "/repair?max_attempts=1",
        json={"specs": ["first==1", "second==1"]},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["suggestions"][0]["changes"][0]["from"] == "first==1"
    assert result["completion_reason"] == "attempt_limit"


@pytest.mark.anyio
async def test_repair_post_applies_the_server_attempt_cap(client, monkeypatch):
    calls = 0

    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        nonlocal calls
        calls += 1
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platform,
                        packages=[],
                        error="Unsatisfiable environment",
                    )
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "MAX_REPAIR_ATTEMPTS", 1)
    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post(
        "/repair?max_attempts=99",
        json={"specs": ["first==1", "second==1"]},
    )

    assert response.status_code == 200
    assert response.json()["completion_reason"] == "attempt_limit"
    assert calls == 2


@pytest.mark.anyio
async def test_repair_post_stops_after_the_suggestion_limit(client, monkeypatch):
    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        solved = specs != ["first==1", "second==1"]
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platform,
                        packages=[],
                        error=None if solved else "Unsatisfiable environment",
                    )
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post(
        "/repair?max_suggestions=1",
        json={"specs": ["first==1", "second==1"]},
    )

    assert response.status_code == 200
    assert response.json()["completion_reason"] == "suggestion_limit"


@pytest.mark.anyio
async def test_repair_post_keeps_verified_results_when_a_candidate_times_out(
    client, monkeypatch
):
    calls = 0

    async def fake_run_solve(request, specs, channels, platforms, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            return app_module.Response(
                app_module.ErrorResponse(error="Solve exceeded timeout"),
                status_code=504,
            )
        solved = calls == 2
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platform,
                        packages=[],
                        error=None if solved else "Unsatisfiable environment",
                    )
                    for platform in platforms
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    response = await client.post(
        "/repair",
        json={"specs": ["first==1", "second==1"]},
    )

    assert response.status_code == 200
    result = response.json()
    assert len(result["suggestions"]) == 1
    assert result["completion_reason"] == "time_limit"


@pytest.mark.anyio
async def test_repair_post_time_budget_includes_solver_queue(
    client, test_app, monkeypatch
):
    worker_started = False

    def fail_run_solve_in_process(*args):
        nonlocal worker_started
        worker_started = True
        raise AssertionError("queued solve must not start after the repair deadline")

    capacity = app_module.ForegroundCapacity(app_module.anyio.CapacityLimiter(1))
    occupied = app_module.anyio.Event()
    release = app_module.anyio.Event()

    async def occupy_solver():
        async with capacity.arrive():
            occupied.set()
            await release.wait()

    monkeypatch.setattr(app_module, "run_solve_in_process", fail_run_solve_in_process)
    test_app.state.solver_limiter = capacity
    async with app_module.anyio.create_task_group() as task_group:
        task_group.start_soon(occupy_solver)
        await occupied.wait()
        response = await client.post(
            "/repair?time_budget_ms=10",
            json={"specs": ["zlib==1.3.1"]},
        )
        release.set()

    assert response.status_code == 504
    assert response.json() == {"error": "Repair exceeded its time budget"}
    assert worker_started is False


@pytest.mark.anyio
async def test_repair_post_returns_unexpected_solver_errors(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("transport failed")

    monkeypatch.setattr("conda_presto.resolve.run_solver", fail)
    response = await client.post("/repair", json={"specs": ["zlib"]})

    assert response.status_code == 500
    assert response.json() == {"error": "Internal solver error"}


@pytest.mark.anyio
async def test_repair_post_rejects_invalid_requests(client):
    unknown = await client.post("/repair", json={"specs": ["zlib"], "unknown": True})
    malformed = await client.post("/repair", json={"specs": ["not[build=]"]})
    invalid_limit = await client.post(
        "/repair?max_attempts=0", json={"specs": ["zlib"]}
    )

    assert unknown.status_code == 400
    assert malformed.status_code == 400
    assert invalid_limit.status_code == 400


@pytest.mark.anyio
async def test_diff_post_compares_two_solve_results(client, monkeypatch):
    async def fake_run_solve(request, specs, channels, platforms, format_name=None):
        version = "1.0" if specs == ["before"] else "2.0"
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platforms[0],
                        packages=[
                            ResolvedPackage(
                                name="demo",
                                version=version,
                                build="0",
                                build_number=0,
                                channel="conda-forge",
                                subdir=platforms[0],
                                url=f"https://example.invalid/demo-{version}.conda",
                                sha256="",
                                md5="",
                                size=None,
                                depends=(),
                                constrains=(),
                            )
                        ],
                    )
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    resp = await client.post(
        "/diff",
        json={
            "from": {"specs": ["before"]},
            "to": {"specs": ["after"]},
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["platforms"] == ["linux-64"]
    assert data["diff"]["linux-64"]["changed"][0]["kind"] == "upgrade"
    assert data["diff"]["linux-64"]["changed"][0]["from"]["version"] == "1.0"
    assert data["diff"]["linux-64"]["changed"][0]["to"]["version"] == "2.0"


@pytest.mark.anyio
async def test_diff_post_rejects_lockfile_materialization_without_solving(
    client, monkeypatch, pixi_lock_v6_text
):
    async def fail_run_solve(*args, **kwargs):
        raise AssertionError("lockfile diff must not solve")

    monkeypatch.setattr(app_module, "run_solve", fail_run_solve)
    request = {
        "file": pixi_lock_v6_text,
        "filename": "pixi.lock",
    }
    resp = await client.post("/diff", json={"from": request, "to": request})

    assert resp.status_code == 400
    assert "cannot be loaded from HTTP input" in resp.json()["error"]


@pytest.mark.anyio
async def test_diff_post_uses_a_declared_platform_for_both_inputs(client, monkeypatch):
    platforms = []

    async def fake_run_solve(request, specs, channels, requested, format_name=None):
        platforms.append(requested)
        return (
            msgspec.json.encode([SolveResult(platform=requested[0], packages=[])]),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    resp = await client.post(
        "/diff",
        json={
            "from": {"specs": ["before"], "platforms": ["linux-64"]},
            "to": {"specs": ["after"]},
        },
    )

    assert resp.status_code == 200
    assert platforms == [["linux-64"], ["linux-64"]]

    inverted = await client.post(
        "/diff",
        json={
            "from": {"specs": ["before"]},
            "to": {"specs": ["after"], "platforms": ["linux-64"]},
        },
    )

    assert inverted.status_code == 200
    assert platforms == [["linux-64"], ["linux-64"], ["linux-64"], ["linux-64"]]


@pytest.mark.anyio
async def test_diff_post_rejects_invalid_and_disjoint_requests(client):
    invalid = await client.post("/diff", content=b"[")
    disjoint = await client.post(
        "/diff",
        json={
            "from": {"specs": ["before"], "platforms": ["linux-64"]},
            "to": {"specs": ["after"], "platforms": ["osx-arm64"]},
        },
    )

    assert invalid.status_code == 400
    assert disjoint.status_code == 400
    assert disjoint.json()["error"] == "The two inputs have no platforms in common"


@pytest.mark.anyio
async def test_diff_post_returns_solver_failures_as_unprocessable(client, monkeypatch):
    async def fake_run_solve(request, specs, channels, platforms, format_name=None):
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platforms[0],
                        packages=[],
                        error="Unsatisfiable environment",
                    )
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    resp = await client.post(
        "/diff",
        json={
            "from": {"specs": ["before"]},
            "to": {"specs": ["after"]},
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error"] == "Unsatisfiable environment"


@pytest.mark.anyio
async def test_diff_post_rejects_uncovered_lockfile_platform(client, pixi_lock_v6_text):
    resp = await client.post(
        "/diff",
        json={
            "from": {"file": pixi_lock_v6_text, "filename": "pixi.lock"},
            "to": {"specs": ["zlib"]},
            "platforms": ["osx-arm64"],
        },
    )

    assert resp.status_code == 400
    assert "Lockfile input cannot be solved" in resp.json()["error"]


@pytest.mark.anyio
async def test_explain_post_returns_requested_dependency_chain(client, monkeypatch):
    async def fake_run_solve(request, specs, channels, platforms, format_name=None):
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platforms[0],
                        packages=[
                            ResolvedPackage(
                                name="app",
                                version="1.0",
                                build="0",
                                build_number=0,
                                channel="conda-forge",
                                subdir=platforms[0],
                                url="",
                                sha256="",
                                md5="",
                                size=None,
                                depends=("library >=1",),
                                constrains=(),
                            ),
                            ResolvedPackage(
                                name="library",
                                version="1.0",
                                build="0",
                                build_number=0,
                                channel="conda-forge",
                                subdir=platforms[0],
                                url="",
                                sha256="",
                                md5="",
                                size=None,
                                depends=(),
                                constrains=(),
                            ),
                        ],
                    )
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    resp = await client.post(
        "/explain",
        json={"package": "library", "specs": ["app"], "platforms": ["linux-64"]},
    )

    assert resp.status_code == 200
    assert resp.json()["chains"] == [["app", "library"]]
    assert resp.json()["complete"] is True


@pytest.mark.anyio
async def test_explain_post_returns_not_found_for_absent_package(client, monkeypatch):
    async def fake_run_solve(request, specs, channels, platforms, format_name=None):
        return (
            msgspec.json.encode([SolveResult(platform=platforms[0], packages=[])]),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    resp = await client.post("/explain", json={"package": "missing", "specs": ["app"]})

    assert resp.status_code == 404
    assert resp.json()["error"] == "Package not found: missing"


@pytest.mark.anyio
async def test_explain_post_rejects_invalid_request_shapes(client):
    invalid = await client.post("/explain", content=b"[")
    missing_package = await client.post("/explain", json={"specs": ["zlib"]})
    unknown_field = await client.post(
        "/explain",
        json={"package": "zlib", "specs": ["zlib"], "unknown": True},
    )
    empty_package = await client.post(
        "/explain", json={"package": "", "specs": ["zlib"]}
    )
    missing_input = await client.post("/explain", json={"package": "zlib"})
    platforms = await client.post(
        "/explain",
        json={
            "package": "zlib",
            "specs": ["zlib"],
            "platforms": ["linux-64", "osx-arm64"],
        },
    )

    assert invalid.status_code == 400
    assert missing_package.status_code == 400
    assert unknown_field.status_code == 400
    assert empty_package.status_code == 400
    assert missing_input.status_code == 400
    assert platforms.status_code == 400
    assert platforms.json()["error"] == "/explain requires exactly one platform"


@pytest.mark.anyio
async def test_explain_post_returns_solver_failure_as_unprocessable(
    client, monkeypatch
):
    async def fake_run_solve(request, specs, channels, platforms, format_name=None):
        return (
            msgspec.json.encode(
                [
                    SolveResult(
                        platform=platforms[0],
                        packages=[],
                        error="Unsatisfiable environment",
                    )
                ]
            ),
            "application/json",
        )

    monkeypatch.setattr(app_module, "run_solve", fake_run_solve)
    resp = await client.post("/explain", json={"package": "zlib", "specs": ["zlib"]})

    assert resp.status_code == 422
    assert resp.json()["error"] == "Unsatisfiable environment"


@pytest.mark.anyio
async def test_transcode_post_rejects_lockfile_materialization(
    client, monkeypatch, pixi_lock_v6_text
):
    def fail_solve(*args, **kwargs):
        raise AssertionError("solver should not run")

    monkeypatch.setattr(app_module, "solve_environments", fail_solve)
    resp = await client.post(
        "/transcode?format=conda-lock-v1",
        json={
            "file": pixi_lock_v6_text,
            "filename": "pixi.lock",
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 400
    assert resp.json() == {
        "error": "Request cannot be transcoded",
        "reasons": ["lockfile package records cannot be loaded from HTTP input"],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "url, file_kind, platforms, extra_body, expected_reasons",
    [
        pytest.param(
            "/transcode?format=pixi-lock-v6",
            "environment",
            ["linux-64"],
            {},
            ["input file is not a lockfile"],
            id="environment-input",
        ),
        pytest.param(
            "/transcode",
            None,
            None,
            {},
            ["no file input was provided", "no output format was requested"],
            id="no-file",
        ),
        pytest.param(
            "/transcode?format=environment-yaml",
            "lockfile",
            ["linux-64"],
            {},
            ["output format is not a lockfile"],
            id="non-lockfile-output",
        ),
        pytest.param(
            "/transcode?format=conda-lock-v1",
            "lockfile",
            ["osx-arm64"],
            {},
            ["requested platforms not present in lockfile: osx-arm64"],
            id="missing-platform",
        ),
        pytest.param(
            "/transcode?format=conda-lock-v1&spec=zlib&channel=conda-forge",
            "lockfile",
            ["linux-64"],
            {"specs": ["python"], "channels": ["defaults"]},
            ["additional specs require solving", "channel overrides require solving"],
            id="specs-and-channels",
        ),
    ],
)
async def test_transcode_post_rejections(
    client,
    pixi_lock_v6_text,
    url,
    file_kind,
    platforms,
    extra_body,
    expected_reasons,
):
    request_body = dict(extra_body)
    if file_kind == "lockfile":
        request_body.update({"file": pixi_lock_v6_text, "filename": "pixi.lock"})
    elif file_kind == "environment":
        request_body.update(
            {
                "file": "channels:\n  - conda-forge\ndependencies:\n  - zlib\n",
                "filename": "environment.yml",
            }
        )
    if platforms is not None:
        request_body["platforms"] = platforms

    if request_body:
        resp = await client.post(url, json=request_body)
    else:
        resp = await client.post(url)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "Request cannot be transcoded"
    for reason in expected_reasons:
        assert reason in body["reasons"]


@pytest.mark.anyio
async def test_transcode_post_unknown_format_returns_400(client, pixi_lock_v6_text):
    resp = await client.post(
        "/transcode?format=does-not-exist",
        json={
            "file": pixi_lock_v6_text,
            "filename": "pixi.lock",
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 400
    assert "Unknown format 'does-not-exist'" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_post_lockfile_missing_platform_without_transcode(
    client, pixi_lock_v6_text
):
    resp = await client.post(
        "/resolve?format=conda-lock-v1",
        json={
            "file": pixi_lock_v6_text,
            "filename": "pixi.lock",
            "platforms": ["osx-arm64"],
        },
    )

    assert resp.status_code == 400
    assert "Lockfile input cannot be solved" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_post_lockfile_extra_specs_fall_back_to_solver(
    client, monkeypatch, pixi_lock_v6_text
):
    calls = []

    def fake_solve_environments(channels, deps, platforms):
        calls.append((channels, deps, platforms))
        return [Environment(platform="linux-64")]

    monkeypatch.setattr(app_module, "solve_environments", fake_solve_environments)
    resp = await client.post(
        "/resolve?format=pixi-lock-v6",
        json={
            "specs": ["zlib"],
            "file": pixi_lock_v6_text,
            "filename": "pixi.lock",
            "platforms": ["linux-64"],
        },
    )

    assert resp.status_code == 200
    assert calls == [(["conda-forge"], ["zlib"], ["linux-64"])]


@pytest.mark.anyio
async def test_resolve_post_raw_body_invalid_utf8(client):
    resp = await client.post(
        "/resolve?platform=linux-64",
        content=b"\xff\xfe not utf-8",
        headers={"content-type": "application/yaml"},
    )
    assert resp.status_code == 400
    assert "UTF-8" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_post_unsupported_content_type(client):
    resp = await client.post(
        "/resolve",
        content=b"anything",
        headers={"content-type": "application/octet-stream"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "Unsupported Content-Type" in body["error"]
    assert "application/json" in body["supported"]
    assert "application/yaml" in body["supported"]


@pytest.mark.anyio
async def test_convert_environment_yml_to_pixi_lock_via_http(client, tmp_path):
    """End-to-end HTTP: POST ``environment.yml`` body -> pixi.lock
    response. Mirrors the CLI pipeline test."""
    platform = "linux-64"
    resp = await client.post(
        "/resolve?format=pixi-lock-v6",
        json={
            "file": (
                "name: demo\nchannels:\n  - conda-forge\ndependencies:\n  - zlib\n"
            ),
            "platforms": [platform],
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")

    data = yaml.safe_load(resp.text)
    assert data["version"] == 6
    assert platform in data["environments"]["default"]["packages"]
    assert "zlib" in resp.text
    for pkg in data["packages"]:
        assert pkg.get("sha256"), "pixi.lock packages must have sha256"


@pytest.mark.anyio
async def test_resolve_format_unknown_returns_400(client):
    resp = await client.get(
        "/resolve",
        params=[
            ("spec", "zlib"),
            ("channel", "conda-forge"),
            ("platform", "linux-64"),
            ("format", "does-not-exist"),
        ],
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "does-not-exist" in body["error"]
    assert isinstance(body["available_formats"], list)
    assert "explicit" in body["available_formats"]


@pytest.mark.anyio
async def test_resolve_format_includes_conda_lockfiles_formats(client):
    """When conda-lockfiles is installed, its formats are exposed."""
    resp = await client.get(
        "/resolve",
        params=[
            ("spec", "zlib"),
            ("channel", "conda-forge"),
            ("platform", "linux-64"),
            ("format", "conda-lock-v1"),
        ],
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    assert "version:" in resp.text or "package:" in resp.text


@pytest.mark.anyio
async def test_resolve_format_propagates_solver_errors_as_500(client, monkeypatch):
    """Exporter path can't represent per-platform errors -> 500 on failure."""

    def boom(*a, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("conda_presto.app.solve_environments", boom)
    resp = await client.post(
        "/resolve?format=explicit",
        json={"specs": ["zlib"], "platforms": ["linux-64"]},
    )
    assert resp.status_code == 500
    assert resp.json()["error"] == "Internal solver error"


@pytest.mark.anyio
async def test_openapi_schema(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "openapi" in data
    assert "/resolve" in data["paths"]
    assert "/preflight" in data["paths"]
    assert "/diff" in data["paths"]
    assert "/explain" in data["paths"]
    assert "/transcode" in data["paths"]
    assert "/parse" in data["paths"]
    assert "/r/{key}" in data["paths"]
    assert "/health" in data["paths"]

    health_endpoint = data["paths"]["/health"]["get"]
    assert {"200", "503"} <= health_endpoint["responses"].keys()
    for status_code in ("200", "503"):
        assert health_endpoint["responses"][status_code]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/HealthResponse")

    preflight = data["paths"]["/preflight"]["post"]
    assert preflight["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/PreflightResult")
    assert {"400", "504"} <= preflight["responses"].keys()

    diff = data["paths"]["/diff"]["post"]
    assert diff["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DiffRequest")
    assert diff["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/DiffResponse")
    assert {"400", "422", "500", "504"} <= diff["responses"].keys()

    explain = data["paths"]["/explain"]["post"]
    assert explain["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ExplainRequest")
    assert explain["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ExplainResult")
    assert {"400", "404", "422", "500", "504"} <= explain["responses"].keys()

    parse_operation = data["paths"]["/parse"]["post"]
    assert parse_operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ParseRequest")
    assert parse_operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ParseResult")
    assert {"400", "504"} <= parse_operation["responses"].keys()


@pytest.mark.anyio
async def test_openapi_routes_are_json_only(client):
    root = await client.get("/")

    assert root.status_code == 200
    assert "json" in root.headers["content-type"]
    assert "openapi" in root.json()
    assert (await client.get("/redoc")).status_code == 404
    assert (await client.get("/swagger")).status_code == 404


@pytest.mark.anyio
async def test_solver_resources_logs_channel_counts_not_credentials(
    monkeypatch,
):
    messages = []
    secret = "https://user:password@example.test/t/private/channel"
    monkeypatch.setattr(app_module, "DEFAULT_CHANNELS", [secret])
    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", False)
    monkeypatch.setattr(app_module, "warmup", lambda *_: None)
    monkeypatch.setattr(
        app_module.log,
        "info",
        lambda message, *args: messages.append(message % args),
    )
    dummy_app = Litestar(route_handlers=[health])

    async with solver_resources_lifespan(dummy_app):
        pass

    output = "\n".join(messages)
    assert "1 channels" in output
    assert "user" not in output
    assert "password" not in output
    assert "private" not in output


@pytest.mark.anyio
async def test_solver_resources_lifespan_initializes_and_cleans_up(monkeypatch):
    warmup_calls = []
    shutdown_calls = []

    def fake_warmup(channels, platforms):
        warmup_calls.append((channels, platforms))

    monkeypatch.setattr(app_module, "warmup", fake_warmup)
    monkeypatch.setattr(
        app_module,
        "shutdown_process_pool",
        lambda: shutdown_calls.append("pool"),
    )
    dummy_app = Litestar(route_handlers=[health])
    async with solver_resources_lifespan(dummy_app):
        assert dummy_app.state.solver_limiter is not None
        assert dummy_app.state.result_cache is not None
        assert warmup_calls == [
            (app_module.DEFAULT_CHANNELS, app_module.DEFAULT_PLATFORMS)
        ]
        assert shutdown_calls == []

    assert shutdown_calls == ["pool"]


@pytest.mark.anyio
async def test_solver_resources_lifespan_starts_persistent_worker(monkeypatch):
    started = []
    stopped = []
    monkeypatch.delenv("CONDA_BROKER_SERVICE_NAME", raising=False)

    def create_worker(channels, platforms, *, restart_on_failure):
        assert restart_on_failure
        return SimpleNamespace(
            running=True,
            start=lambda: started.append((channels, platforms)),
            shutdown=lambda: stopped.append("shutdown"),
        )

    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", True)
    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
    dummy_app = Litestar(route_handlers=[health])

    async with solver_resources_lifespan(dummy_app):
        assert started == [
            (app_module.DEFAULT_CHANNELS, app_module.DEFAULT_PLATFORMS),
        ]
        assert dummy_app.state.solve_worker.running
        assert stopped == []

    assert stopped == ["shutdown"]


@pytest.mark.anyio
async def test_solver_resources_shutdowns_pool_when_worker_cleanup_fails(
    monkeypatch,
):
    events = []

    def shutdown():
        events.append("worker-shutdown")
        raise RuntimeError("cleanup failed")

    worker = SimpleNamespace(
        start=lambda: events.append("worker-start"),
        shutdown=shutdown,
    )
    monkeypatch.setattr(app_module, "RESULT_CACHE_BACKEND", "memory")
    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", True)
    monkeypatch.setattr(app_module, "PersistentSolveWorker", lambda *_a, **_kw: worker)
    monkeypatch.setattr(
        app_module,
        "shutdown_process_pool",
        lambda: events.append("pool-stop"),
    )
    app = Litestar(route_handlers=[health])

    async with solver_resources_lifespan(app):
        pass

    assert events == ["worker-start", "worker-shutdown", "pool-stop"]


@pytest.mark.anyio
async def test_solver_resources_shields_store_close_during_cancellation(monkeypatch):
    events = []
    entered = anyio.Event()

    class Store:
        async def __aenter__(self):
            events.append("store-open")
            return self

        async def __aexit__(self, *_):
            events.append("store-close-start")
            await anyio.lowlevel.checkpoint()
            events.append("store-close-finish")

    store = Store()
    monkeypatch.setattr(app_module, "RESULT_CACHE_BACKEND", "file")
    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", False)
    monkeypatch.setattr(app_module, "warmup", lambda *_: None)
    monkeypatch.setattr(
        app_module,
        "shutdown_process_pool",
        lambda: events.append("pool-stop"),
    )
    monkeypatch.setattr(
        ResultCache,
        "store_for_config",
        staticmethod(lambda *_args: store),
    )
    app = Litestar(route_handlers=[health])

    async def run_lifespan() -> None:
        async with solver_resources_lifespan(app):
            entered.set()
            await anyio.sleep_forever()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run_lifespan)
        await entered.wait()
        tasks.cancel_scope.cancel()

    assert events == [
        "store-open",
        "pool-stop",
        "store-close-start",
        "store-close-finish",
    ]


@pytest.mark.anyio
async def test_solver_resources_leaves_worker_recovery_to_broker(monkeypatch):
    captured = {}

    def create_worker(channels, platforms, *, restart_on_failure):
        captured["restart_on_failure"] = restart_on_failure
        return SimpleNamespace(
            running=True,
            start=lambda: None,
            shutdown=lambda: None,
        )

    monkeypatch.setenv("CONDA_BROKER_SERVICE_NAME", "conda-presto.server")
    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", True)
    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
    dummy_app = Litestar(route_handlers=[health])

    async with solver_resources_lifespan(dummy_app):
        assert captured == {"restart_on_failure": False}


@pytest.mark.anyio
async def test_formats_endpoint(client):
    resp = await client.get("/formats")
    assert resp.status_code == 200
    data = resp.json()
    assert "formats" in data
    assert isinstance(data["formats"], list)
    assert "explicit" in data["formats"]
    assert "environment-yaml" in data["formats"]


@pytest.mark.anyio
async def test_platforms_endpoint(client):
    resp = await client.get("/platforms")
    assert resp.status_code == 200
    data = resp.json()
    assert "platforms" in data
    assert isinstance(data["platforms"], list)
    assert "linux-64" in data["platforms"]
    assert "osx-arm64" in data["platforms"]
    assert "win-64" in data["platforms"]
    assert data["platforms"] == sorted(data["platforms"])


@pytest.mark.anyio
async def test_version_endpoint(client):
    resp = await client.get("/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "conda-presto" in data
    assert "conda" in data


@pytest.mark.anyio
async def test_parse_endpoint(client, test_app):
    test_app.state.solver_limiter = app_module.ForegroundCapacity(
        anyio.CapacityLimiter(1)
    )
    yml = (
        "name: test\n"
        "channels:\n"
        "  - conda-forge\n"
        "dependencies:\n"
        "  - python=3.12\n"
        "  - numpy\n"
    )
    resp = await client.post(
        "/parse",
        json={"file": yml, "filename": "environment.yml"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "specs" in data
    assert "channels" in data
    assert "python=3.12" in data["specs"]
    assert "numpy" in data["specs"]
    assert "conda-forge" in data["channels"]
    assert test_app.state.solver_limiter.generation == 1


@pytest.mark.anyio
async def test_parse_endpoint_rejects_too_many_specs(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.MAX_SPECS", 2)
    yml = "name: test\nchannels:\n  - conda-forge\ndependencies:\n  - a\n  - b\n  - c\n"
    resp = await client.post(
        "/parse",
        json={"file": yml, "filename": "environment.yml"},
    )
    assert resp.status_code == 400
    assert "Too many specs" in resp.json()["error"]


@pytest.mark.anyio
async def test_parse_endpoint_rejects_structurally_oversized_input(client):
    response = await client.post(
        "/parse",
        json={
            "file": "dependencies:\n" + "  - zlib\n" * 10_001,
            "filename": "environment.yml",
        },
    )

    assert response.status_code == 400
    assert "structural complexity limit" in response.json()["error"]


@pytest.mark.anyio
async def test_parse_endpoint_rejects_explicit_lockfile(client):
    explicit = "@EXPLICIT\nhttps://example.invalid/linux-64/pkg-1.0-0.conda\n"
    resp = await client.post(
        "/parse",
        json={"file": explicit, "filename": "explicit.txt"},
    )
    assert resp.status_code == 400
    assert "Explicit package URL lockfiles" in resp.json()["error"]


@pytest.mark.anyio
async def test_resolve_lockfile_does_not_fetch_package_urls(client):
    requests = []
    payload = b"not a conda package"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_HEAD(self):
            requests.append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    package_url = f"http://127.0.0.1:{server.server_address[1]}/probe-1.0-0.conda"
    lockfile = f"""\
version: 6
environments:
  default:
    channels: []
    packages:
      linux-64:
        - conda: {package_url}
packages:
  - conda: {package_url}
"""

    try:
        response = await client.post(
            "/resolve",
            json={
                "file": lockfile,
                "filename": "pixi.lock",
                "platforms": ["linux-64"],
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()

    assert response.status_code == 400
    assert "cannot be loaded from HTTP input" in response.json()["error"]
    assert requests == []


@pytest.mark.anyio
async def test_parse_endpoint_does_not_expose_temporary_path(client):
    explicit = "\ufeff@EXPLICIT\nhttps://example.invalid/linux-64/pkg-1.0-0.conda\n"

    response = await client.post(
        "/parse",
        json={"file": explicit, "filename": "explicit.txt"},
    )

    assert response.status_code == 400
    assert "Explicit package URL lockfiles" in response.json()["error"]
    assert tempfile.gettempdir() not in response.text


@pytest.mark.anyio
async def test_parse_endpoint_does_not_expand_server_environment(client, monkeypatch):
    monkeypatch.setenv("CONDA_PRESTO_TEST_SECRET", "super-secret-value")

    response = await client.post(
        "/parse",
        json={
            "file": (
                "channels:\n"
                "  - https://${CONDA_PRESTO_TEST_SECRET}@conda.anaconda.org/conda-forge\n"
                "dependencies:\n"
                "  - zlib\n"
            ),
            "filename": "environment.yml",
        },
    )

    assert response.status_code == 200
    assert "super-secret-value" not in response.text
    assert response.json()["channels"] == [
        "https://${CONDA_PRESTO_TEST_SECRET}@conda.anaconda.org/conda-forge"
    ]


@pytest.mark.anyio
async def test_parse_endpoint_suppresses_parser_output(client, capfd):
    marker = "workflow-command-injection-marker"

    response = await client.post(
        "/parse",
        json={
            "file": (f"'::warning::{marker}': value\ndependencies:\n  - zlib\n"),
            "filename": "environment.yml",
        },
    )

    assert response.status_code == 200
    captured = capfd.readouterr()
    assert marker not in captured.out
    assert marker not in captured.err


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "error"),
    [
        pytest.param(f"{'a' * 10_000}.yml", "Input filename is too long", id="long"),
        pytest.param(
            "environment\n.yml",
            "Input filename contains unsupported characters",
            id="non-printable",
        ),
    ],
)
async def test_parse_endpoint_rejects_unsafe_filename_without_echoing_it(
    client,
    filename,
    error,
):

    response = await client.post(
        "/parse",
        json={"file": "dependencies:\n  - zlib\n", "filename": filename},
    )

    assert response.status_code == 400
    assert response.json()["error"] == error
    assert len(response.text) < 100


@pytest.mark.parametrize(
    ("outcome", "status"),
    [
        pytest.param("parsed", "ok", id="success"),
        pytest.param(ValueError, "invalid", id="invalid"),
        pytest.param(RuntimeError, "error", id="internal-error"),
    ],
)
def test_input_parse_process_entrypoint_sends_sanitized_result(
    monkeypatch,
    tmp_path,
    outcome,
    status,
):
    path = tmp_path / "environment.yml"
    path.write_text("dependencies:\n  - zlib\n")
    sent = []
    sender = SimpleNamespace(send=sent.append, close=lambda: sent.append("closed"))

    def parse(*_, **__):
        if outcome is ValueError:
            raise ValueError(f"Invalid {path} from https://user:secret@example.test")
        if outcome is RuntimeError:
            raise RuntimeError("internal parser failure")
        return outcome

    monkeypatch.setattr(inputs_module.os, "environ", {})
    monkeypatch.setattr(ParsedInputFile, "from_path", parse)

    ParsedInputFile._from_path_process(
        sender,
        path,
        None,
        time.monotonic() + 1,
    )

    assert sent[-1] == "closed"
    result_status, payload = sent[0]
    assert result_status == status
    if status == "ok":
        assert payload == "parsed"
    elif status == "invalid":
        assert str(path) not in payload
        assert "[redacted-url]" in payload
    else:
        assert payload is None


@pytest.mark.parametrize(
    ("filename", "content", "limit", "error"),
    [
        pytest.param(
            "environment.yml",
            "dependencies: &dependencies\n  - zlib\ncopy: *dependencies\n",
            10,
            "YAML aliases are not accepted",
            id="yaml-alias",
        ),
        pytest.param(
            "environment.json",
            '{"dependencies":["a","b","c"]}',
            3,
            "structural complexity limit",
            id="compact-json",
        ),
        pytest.param(
            "pixi.toml",
            'dependencies = ["a", "b", "c"]\n',
            3,
            "structural complexity limit",
            id="compact-toml",
        ),
        pytest.param(
            "specs.txt",
            "a\nb\nc\n",
            2,
            "structural complexity limit",
            id="text-lines",
        ),
    ],
)
def test_input_parse_process_rejects_unsafe_complexity(
    monkeypatch,
    tmp_path,
    filename,
    content,
    limit,
    error,
):
    path = tmp_path / filename
    path.write_text(content)
    sent = []
    sender = SimpleNamespace(send=sent.append, close=lambda: None)

    monkeypatch.setattr(inputs_module.os, "environ", {})
    monkeypatch.setattr(inputs_module, "HTTP_INPUT_MAX_NODES", limit)

    ParsedInputFile._from_path_process(
        sender,
        path,
        None,
        time.monotonic() + 1,
    )

    status, payload = sent[0]
    assert status == "invalid"
    assert error in payload


@pytest.mark.anyio
async def test_parse_endpoint_timeout(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.PARSE_TIMEOUT_S", 0.1)

    def timed_out(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(
        "conda_presto.app.ParsedInputFile.from_content_until",
        timed_out,
    )
    resp = await client.post(
        "/parse",
        json={
            "file": "dependencies:\n  - zlib\n",
            "filename": "environment.yml",
        },
    )
    assert resp.status_code == 504
    assert "timeout" in resp.json()["error"].lower()


def test_parse_timeout_terminates_and_kills_the_parser_process(monkeypatch):
    calls = []
    parser_paths = []
    alive = iter((True, True))
    receiver = SimpleNamespace(
        poll=lambda _timeout: False,
        close=lambda: calls.append("receiver-close"),
    )
    sender = SimpleNamespace(close=lambda: calls.append("sender-close"))
    process = SimpleNamespace(
        exitcode=None,
        start=lambda: calls.append("start"),
        is_alive=lambda: next(alive, False),
        terminate=lambda: calls.append("terminate"),
        kill=lambda: calls.append("kill"),
        join=lambda _timeout=None: calls.append("join"),
    )

    def create_process(**kwargs):
        parser_paths.append(kwargs["args"][1])
        assert parser_paths[0].is_file()
        return process

    process_context = SimpleNamespace(
        Pipe=lambda **_: (receiver, sender),
        Process=create_process,
    )
    monkeypatch.setattr(
        inputs_module.multiprocessing,
        "get_context",
        lambda _: process_context,
    )

    with pytest.raises(TimeoutError):
        ParsedInputFile.from_content_until(
            "dependencies:\n  - zlib\n",
            "environment.yml",
            None,
            time.monotonic() + 1,
        )

    assert calls == [
        "start",
        "sender-close",
        "receiver-close",
        "terminate",
        "join",
        "kill",
        "join",
    ]
    assert not parser_paths[0].parent.exists()


def test_parse_start_failure_closes_pipes_and_process(monkeypatch):
    calls = []
    parser_paths = []
    receiver = SimpleNamespace(close=lambda: calls.append("receiver-close"))
    sender = SimpleNamespace(close=lambda: calls.append("sender-close"))

    def start_process():
        raise OSError("spawn failed")

    process = SimpleNamespace(
        start=start_process,
        is_alive=lambda: True,
        terminate=lambda: calls.append("terminate"),
        join=lambda timeout=None: calls.append(f"join:{timeout}"),
    )

    def create_process(**kwargs):
        parser_paths.append(kwargs["args"][1])
        return process

    process_context = SimpleNamespace(
        Pipe=lambda **_: (receiver, sender),
        Process=create_process,
    )
    monkeypatch.setattr(
        inputs_module.multiprocessing,
        "get_context",
        lambda _: process_context,
    )

    with pytest.raises(OSError, match="spawn failed"):
        ParsedInputFile.from_content_until(
            "dependencies:\n  - zlib\n",
            "environment.yml",
            None,
            time.monotonic() + 1,
        )

    assert calls == ["receiver-close", "sender-close", "terminate", "join:5"]
    assert not parser_paths[0].parent.exists()


@pytest.mark.anyio
async def test_resolve_file_rejects_explicit_lockfile(client, monkeypatch):
    def fail_solve(*args, **kwargs):
        raise AssertionError("solve should not run")

    monkeypatch.setattr("conda_presto.app.solve", fail_solve)
    explicit = "@EXPLICIT\nhttps://example.invalid/linux-64/pkg-1.0-0.conda\n"
    resp = await client.post(
        "/resolve",
        json={
            "file": explicit,
            "filename": "explicit.txt",
            "platforms": ["linux-64"],
        },
    )
    assert resp.status_code == 400
    assert "Explicit package URL lockfiles" in resp.json()["error"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="missing-file"),
        pytest.param(
            {"file": "dependencies:\n  - zlib\n", "unknown": True},
            id="unknown-field",
        ),
    ],
)
async def test_parse_endpoint_rejects_invalid_request_shapes(client, body):
    resp = await client.post("/parse", json=body)
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_parse_endpoint_empty_body(client):
    resp = await client.post(
        "/parse",
        content=b"",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
