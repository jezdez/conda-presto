"""Tests for conda_presto.app (Litestar endpoints)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import anyio
import msgspec
import pytest
import yaml
from conda.exceptions import PackagesNotFoundError
from conda.models.environment import Environment
from conda.plugins.types import EnvironmentFormat
from httpx import ASGITransport, AsyncClient
from litestar import Litestar
from litestar.openapi import OpenAPIConfig
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

import conda_presto.app as app_module
import conda_presto.cache as cache_module
from conda_presto.app import (
    build_cors_config,
    diff_post,
    explain_post,
    formats,
    health,
    on_shutdown,
    on_startup,
    parse,
    platforms,
    preflight_post,
    repair_post,
    resolve_get,
    resolve_post,
    result_get,
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
    monkeypatch.setattr(app_module, "SOLVER_ENDPOINT", True)
    monkeypatch.setenv("CONDA_BROKER_SERVICE_NAME", PrestoSolverClient.service_name)


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


@pytest.fixture()
def presto_solver_outcome():
    def create(response, metadata_before, metadata_used=None):
        return PrestoSolveOutcome(
            result=response,
            metadata_before=metadata_before,
            metadata_used=metadata_used or metadata_before,
        )

    return create


@pytest.mark.anyio
async def test_solver_v1_is_disabled_by_default(client, presto_solver_request):
    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 404


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
    monkeypatch.setattr(app_module, "SOLVER_ENDPOINT", True)
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
async def test_solver_v1_caches_successful_final_state(
    client,
    test_app,
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    enabled_solver_endpoint,
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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

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
    assert "location" not in second.headers
    assert [data for data, _ in calls] == [presto_solver_request]
    assert calls[0][1] > time.monotonic()


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
    test_app.state.solver_limiter = limiter

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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

    async def occupy_limiter():
        async with test_app.state.solver_limiter:
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

    def solve(data, timeout):
        calls.append((data, timeout))
        time.sleep(0.05)
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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(post_solve)
        tasks.start_soon(post_solve)

    assert [response.status_code for response in responses] == [200, 200]
    assert [data for data, _ in calls] == [presto_solver_request]


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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

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


@pytest.mark.anyio
async def test_solver_v1_bypasses_stale_cached_state(
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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    refreshed_key = ResultCache.solver_key(presto_solver_request.cache_key())
    assert response.status_code == 200
    assert response.json()["records"] == [{"name": "zlib"}]
    assert [data for data, _ in calls] == [presto_solver_request]
    assert refreshed_key in test_app.state.result_cache.entries


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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

    response = await client.post(
        "/solver/v1",
        content=msgspec.json.encode(presto_solver_request),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["records"] == [{"name": "zlib"}]
    assert not test_app.state.result_cache.entries


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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

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


def test_solver_v1_is_excluded_from_request_logging():
    logging_config = app_module.middleware[0].kwargs["config"]

    assert logging_config.exclude == r"^/solver/v1$"


def test_build_cors_config_enabled_for_explicit_origins():
    cors = build_cors_config(["https://app.example.com"])
    assert cors is not None
    assert cors.allow_origins == ["https://app.example.com"]


def test_resolve_input_retains_conda_lock_main_category():
    source = app_module.ResolveInput(
        specs=[],
        channels=[],
        platforms=["linux-64"],
        parsed_file=ParsedInputFile(
            specs=[],
            channels=[],
            environment_format=EnvironmentFormat.lockfile,
            source_format="conda-lock-v1",
        ),
        direct_lockfile=True,
    )

    assert source.lockfile_category == "main"


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
    assert first.headers["cache-control"] == cache_module.RESULT_CACHE_CONTROL
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
    assert cached.headers["cache-control"] == cache_module.RESULT_CACHE_CONTROL


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
    app = Litestar(
        route_handlers=[resolve_post, result_get],
        stores={cache_module.RESULT_CACHE_STORE_NAME: MemoryStore()},
    )
    app.state.solver_limiter = None
    app.state.result_cache = ResultCache(
        max_size=256,
        store_name=cache_module.RESULT_CACHE_STORE_NAME,
    )
    calls = 0

    def fake_solve(channels, specs, platforms, **kwargs):
        nonlocal calls
        calls += 1
        return [SolveResult(platform="linux-64", packages=[])]

    monkeypatch.setattr(app_module, "solve", fake_solve)

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
            store_name=cache_module.RESULT_CACHE_STORE_NAME,
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
    app = Litestar(
        route_handlers=[solver_v1, result_get],
        stores={cache_module.RESULT_CACHE_STORE_NAME: MemoryStore()},
    )
    app.state.solver_limiter = anyio.CapacityLimiter(1)
    app.state.result_cache = ResultCache(
        max_size=256,
        store_name=cache_module.RESULT_CACHE_STORE_NAME,
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
            store_name=cache_module.RESULT_CACHE_STORE_NAME,
        )
        second = await store_client.post(
            "/solver/v1",
            content=msgspec.json.encode(presto_solver_request),
            headers={"content-type": "application/json"},
        )
        public = await store_client.get(f"/r/{presto_solver_request.cache_key()}")
        digest = presto_solver_request.cache_key()
        store = app.stores.get(cache_module.RESULT_CACHE_STORE_NAME)
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
@pytest.mark.parametrize(
    "backend",
    [pytest.param("memory", id="memory"), pytest.param("file", id="file")],
)
async def test_solver_cache_replaces_one_persistent_entry(
    monkeypatch,
    tmp_path,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    backend,
):
    store = (
        MemoryStore()
        if backend == "memory"
        else FileStore(tmp_path, create_directories=True)
    )
    cache = ResultCache(max_size=256)
    previous = fresh_repodata_snapshot
    stale = RepodataSnapshot(previous.records, True)
    current = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    snapshots = iter([previous, current])
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: next(snapshots),
    )

    first, first_status = await cache.publish_solver(
        presto_solver_request,
        presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "old"}], neutered=[]),
            previous,
        ),
        store,
    )
    second, second_status = await cache.publish_solver(
        presto_solver_request,
        presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
            stale,
            current,
        ),
        store,
    )

    key = ResultCache.solver_key(presto_solver_request.cache_key())
    payload = await store.get(key)
    stored = msgspec.msgpack.decode(payload, type=cache_module.StoredSolverResult)
    assert first_status == second_status == "published"
    assert first.metadata_used == previous
    assert second.metadata_used == current
    assert list(cache.entries) == [key]
    assert stored.response.records == [{"name": "new"}]
    assert stored.metadata_used == current


@pytest.mark.anyio
async def test_solver_cache_keeps_matching_entry_during_publication(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
):
    cache = ResultCache(max_size=256)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    current = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "current"}], neutered=[]),
        metadata_used=fresh_repodata_snapshot,
    )
    cache.remember_memory(key, current)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )

    stored, status = await cache.publish_solver(
        presto_solver_request,
        presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "late"}], neutered=[]),
            fresh_repodata_snapshot,
        ),
    )

    assert status == "already-current"
    assert stored is current
    assert cache.entries[key].response.records == [{"name": "current"}]


@pytest.mark.anyio
async def test_solver_cache_rejects_corrupt_typed_envelope(
    presto_solver_request,
):
    store = MemoryStore()
    cache = ResultCache(max_size=256)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    await store.set(
        key,
        msgspec.msgpack.encode(
            {
                "response": {"records": "not-a-list", "neutered": []},
                "metadata_used": {"records": [], "stale": False},
                "version": 1,
            }
        ),
    )

    stored = await cache.get_solver_result(presto_solver_request, store)

    assert stored is None
    assert await store.get(key) is None


@pytest.mark.anyio
async def test_solver_cache_serializes_persistent_read_and_publication(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
):
    previous = fresh_repodata_snapshot
    stale = RepodataSnapshot(previous.records, True)
    current = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    old = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "old"}], neutered=[]),
        metadata_used=previous,
    )
    started = anyio.Event()
    release = anyio.Event()

    class Store:
        value = msgspec.msgpack.encode(old)

        async def get(self, key):
            started.set()
            await release.wait()
            return self.value

        async def set(self, key, value):
            self.value = value

        async def delete(self, key):
            self.value = None

    store = Store()
    cache = ResultCache(max_size=256)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: current,
    )
    publication = []

    async def read():
        await cache.get_solver_result(presto_solver_request, store)

    async def publish():
        publication.append(
            await cache.publish_solver(
                presto_solver_request,
                presto_solver_outcome(
                    PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
                    stale,
                    current,
                ),
                store,
            )
        )

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(read)
        await started.wait()
        tasks.start_soon(publish)
        await anyio.sleep(0)
        release.set()

    key = ResultCache.solver_key(presto_solver_request.cache_key())
    assert publication[0][1] == "published"
    assert cache.entries[key].response.records == [{"name": "new"}]
    stored = msgspec.msgpack.decode(
        store.value,
        type=cache_module.StoredSolverResult,
    )
    assert stored.response.records == [{"name": "new"}]


def test_result_cache_stores_for_config_registers_file_store(tmp_path):
    stores = ResultCache.stores_for_config(
        "file",
        str(tmp_path),
        None,
        "conda-presto",
    )

    assert isinstance(stores[cache_module.RESULT_CACHE_STORE_NAME], FileStore)


def test_result_cache_stores_for_config_registers_redis_store():
    stores = ResultCache.stores_for_config(
        "redis",
        None,
        "redis://localhost:6379/0",
        "conda-presto-test",
    )

    assert isinstance(stores[cache_module.RESULT_CACHE_STORE_NAME], RedisStore)


@pytest.mark.parametrize(
    "backend, error",
    [
        pytest.param("file", "CONDA_PRESTO_RESULT_CACHE_DIR", id="file-dir"),
        pytest.param("sqlite", "Unsupported result cache backend", id="unknown"),
    ],
)
def test_result_cache_stores_for_config_rejects_invalid_config(backend, error):
    with pytest.raises(ValueError, match=error):
        ResultCache.stores_for_config(backend, None, None, "conda-presto")


@pytest.mark.parametrize(
    "max_bytes, writes, expected_retained, expected_keys, expected_bytes",
    [
        pytest.param(
            30,
            (("first", 20), ("second", 20)),
            (True, True),
            ["second"],
            30,
            id="evict-oldest-by-bytes",
        ),
        pytest.param(
            100,
            (("same", 10), ("same", 20)),
            (True, True),
            ["same"],
            30,
            id="replace-existing-accounting",
        ),
        pytest.param(
            10,
            (("oversized", 20),),
            (False,),
            [],
            0,
            id="skip-oversized",
        ),
        pytest.param(
            20,
            (("same", 5), ("same", 20)),
            (True, False),
            [],
            0,
            id="drop-existing-oversized-replacement",
        ),
    ],
)
def test_result_cache_memory_limit(
    max_bytes,
    writes,
    expected_retained,
    expected_keys,
    expected_bytes,
):
    cache = ResultCache(max_size=10, max_bytes=max_bytes)

    retained = tuple(
        cache.remember_memory(
            key,
            cache_module.StoredResult(b"x" * body_size, "text/plain"),
        )
        for key, body_size in writes
    )

    assert retained == expected_retained
    assert list(cache.entries) == expected_keys
    assert cache.current_bytes == expected_bytes


@pytest.mark.anyio
async def test_result_cache_remember_omits_permalink_when_memory_rejects_result():
    cache = ResultCache(max_size=10, max_bytes=10)

    response = await cache.remember("oversized", b"x" * 20, "text/plain")

    assert "Location" not in response.headers
    assert not cache.entries


@pytest.mark.anyio
async def test_result_cache_ignores_failed_corrupt_entry_cleanup():
    class Store:
        async def get(self, key):
            return b"corrupt"

        async def delete(self, key):
            raise RuntimeError("store unavailable")

    cache = ResultCache(max_size=10)

    assert await cache.get_response("key", Store()) is None


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


def test_different_output_formats_produce_different_cache_keys():
    default_key = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)
    explicit_key = ResultCache.key_for(
        ["zlib"], ["conda-forge"], ["linux-64"], "explicit"
    )

    assert explicit_key != default_key


@pytest.mark.parametrize("package_name", cache_module.CACHE_DEPENDENCY_PACKAGES)
def test_different_dependency_versions_produce_different_cache_keys(
    monkeypatch,
    package_name,
):
    def version_one(package):
        if package == package_name:
            return "one"
        return "test"

    def version_two(package):
        if package == package_name:
            return "two"
        return "test"

    monkeypatch.setattr(cache_module, "pkg_version", version_one)
    first = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)
    monkeypatch.setattr(cache_module, "pkg_version", version_two)
    second = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)

    assert second != first


def test_virtual_package_overrides_produce_different_cache_keys(monkeypatch):
    first = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)
    monkeypatch.setitem(cache_module.VIRTUAL_PACKAGES["linux"], "glibc", "9.9")
    second = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)

    assert second != first


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
    test_app.state.solver_limiter = app_module.anyio.CapacityLimiter(1)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve",
            json={"specs": ["zlib"], "platforms": ["linux-64"]},
        )

    assert response.status_code == 200
    assert captured["limiter"] is test_app.state.solver_limiter
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
    test_app.state.solver_limiter = limiter

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
    test_app.state.solver_limiter = app_module.anyio.CapacityLimiter(1)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve",
            json={"specs": ["zlib"], "platforms": ["linux-64"]},
        )

    assert response.status_code == 200
    assert calls == [
        (["conda-forge"], ["zlib"], ["linux-64"], None, 60),
    ]


@pytest.mark.anyio
async def test_run_solve_passes_per_call_timeout(test_app):
    calls = []

    def solve(*args):
        calls.append(args)
        return []

    test_app.state.solve_worker = SimpleNamespace(solve=solve)

    response = await app_module.run_solve(
        SimpleNamespace(app=test_app),
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        timeout_s=0.25,
    )

    assert response == (b"[]", "application/json")
    assert calls == [
        (["conda-forge"], ["zlib"], ["linux-64"], None, 0.25),
    ]


@pytest.mark.anyio
async def test_run_solve_timeout_includes_persistent_worker_queue(test_app):
    calls = []
    occupied = anyio.Event()
    release = anyio.Event()
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)
    test_app.state.solve_worker = SimpleNamespace(
        solve=lambda *_: calls.append("solve") or []
    )

    async def occupy_limiter():
        async with test_app.state.solver_limiter:
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
    assert calls == ["start", "sender.close", "receiver.close", "join:None"]


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
        "join:None",
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

    limiter = app_module.anyio.CapacityLimiter(1)
    occupied = app_module.anyio.Event()
    release = app_module.anyio.Event()

    async def occupy_solver():
        async with limiter:
            occupied.set()
            await release.wait()

    monkeypatch.setattr(app_module, "run_solve_in_process", fail_run_solve_in_process)
    test_app.state.solver_limiter = limiter
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
@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        pytest.param(
            "/repair",
            {"specs": ["zlib"], "unknown": True},
            {
                "status_code": 400,
                "detail": "Validation failed for POST /repair",
                "extra": [
                    {
                        "message": "Object contains unknown field `unknown`",
                        "key": "data",
                        "source": "body",
                    }
                ],
            },
            id="unknown-body-field",
        ),
        pytest.param(
            "/repair",
            {"specs": ["not[build=]"]},
            {
                "error": (
                    "Invalid spec 'not[build=]': key-value mismatch in brackets; "
                    "a key or a value is missing"
                )
            },
            id="malformed-match-spec",
        ),
        pytest.param(
            "/repair?max_attempts=0",
            {"specs": ["zlib"]},
            {
                "status_code": 400,
                "detail": "Validation failed for POST /repair?max_attempts=0",
                "extra": [
                    {
                        "message": "Expected `int` >= 1",
                        "key": "max_attempts",
                        "source": "query",
                    }
                ],
            },
            id="attempt-limit-below-minimum",
        ),
    ],
)
async def test_repair_post_rejects_invalid_requests(client, path, body, expected):
    response = await client.post(path, json=body)

    assert response.status_code == 400
    assert response.json() == expected


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
async def test_diff_post_reads_lockfiles_without_solving(
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

    assert resp.status_code == 200
    assert resp.json()["diff"]["linux-64"]["unchanged_count"] == 2


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
@pytest.mark.parametrize(
    "url, request_kind, expected_version, content_type",
    [
        pytest.param(
            "/transcode?format=conda-lock-v1",
            "json",
            1,
            None,
            id="json-to-conda-lock",
        ),
        pytest.param(
            "/transcode?format=pixi-lock-v6&filename=pixi.lock&platform=linux-64",
            "raw",
            6,
            "application/yaml",
            id="raw-to-pixi-lock",
        ),
        pytest.param(
            "/transcode?format=pixi-lock-v6&filename=pixi.lock&platform=linux-64",
            "raw",
            6,
            "application/yaml; charset=utf-8",
            id="raw-with-content-type-parameters",
        ),
    ],
)
async def test_transcode_post_lockfile_to_lockfile_without_solver(
    client,
    monkeypatch,
    pixi_lock_v6_text,
    url,
    request_kind,
    expected_version,
    content_type,
):
    def fail_solve(*args, **kwargs):
        raise AssertionError("solver should not run")

    monkeypatch.setattr(app_module, "solve_environments", fail_solve)
    if request_kind == "json":
        resp = await client.post(
            url,
            json={
                "file": pixi_lock_v6_text,
                "filename": "pixi.lock",
                "platforms": ["linux-64"],
            },
        )
    else:
        resp = await client.post(
            url,
            content=pixi_lock_v6_text,
            headers={"content-type": content_type},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    data = yaml.safe_load(resp.text)
    assert data["version"] == expected_version
    if expected_version == 1:
        assert data["metadata"]["platforms"] == ["linux-64"]
        assert {pkg["name"] for pkg in data["package"]} == {"libzlib", "zlib"}
    else:
        assert "linux-64" in data["environments"]["default"]["packages"]


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
async def test_on_shutdown_shuts_down_process_pool(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "conda_presto.app.shutdown_process_pool",
        lambda: calls.append(True),
    )
    dummy_app = Litestar(route_handlers=[health])
    await on_shutdown(dummy_app)
    assert calls == [True]


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
async def test_on_startup_initializes(monkeypatch):
    warmup_calls = []

    def fake_warmup(channels, platforms):
        warmup_calls.append((channels, platforms))

    monkeypatch.setattr(app_module, "warmup", fake_warmup)
    dummy_app = Litestar(route_handlers=[health])
    await on_startup(dummy_app)
    assert dummy_app.state.solver_limiter is not None
    assert dummy_app.state.result_cache is not None
    assert len(warmup_calls) == 1


@pytest.mark.anyio
async def test_on_startup_starts_persistent_worker(monkeypatch):
    started = []
    monkeypatch.delenv("CONDA_BROKER_SERVICE_NAME", raising=False)

    def create_worker(channels, platforms, *, restart_on_failure):
        assert restart_on_failure
        return SimpleNamespace(
            running=True,
            start=lambda: started.append((channels, platforms)),
        )

    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", True)
    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
    dummy_app = Litestar(route_handlers=[health])

    await on_startup(dummy_app)

    assert started == [
        (app_module.DEFAULT_CHANNELS, app_module.DEFAULT_PLATFORMS),
    ]
    assert dummy_app.state.solve_worker.running


@pytest.mark.anyio
async def test_on_startup_leaves_broker_worker_recovery_to_broker(monkeypatch):
    captured = {}

    def create_worker(channels, platforms, *, restart_on_failure):
        captured["restart_on_failure"] = restart_on_failure
        return SimpleNamespace(running=True, start=lambda: None)

    monkeypatch.setenv("CONDA_BROKER_SERVICE_NAME", "conda-presto.server")
    monkeypatch.setattr(app_module, "PERSISTENT_WORKER", True)
    monkeypatch.setattr(app_module, "PersistentSolveWorker", create_worker)
    dummy_app = Litestar(route_handlers=[health])

    await on_startup(dummy_app)

    assert captured == {"restart_on_failure": False}


@pytest.mark.anyio
async def test_on_shutdown_shuts_down_persistent_worker(monkeypatch):
    calls = []
    dummy_app = Litestar(route_handlers=[health])
    dummy_app.state.solve_worker = SimpleNamespace(
        shutdown=lambda: calls.append("worker")
    )
    monkeypatch.setattr(
        app_module, "shutdown_process_pool", lambda: calls.append("pool")
    )

    await on_shutdown(dummy_app)

    assert calls == ["worker", "pool"]


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
async def test_parse_endpoint(client):
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
async def test_parse_endpoint_rejects_explicit_lockfile(client):
    explicit = "@EXPLICIT\nhttps://example.invalid/linux-64/pkg-1.0-0.conda\n"
    resp = await client.post(
        "/parse",
        json={"file": explicit, "filename": "explicit.txt"},
    )
    assert resp.status_code == 400
    assert "Explicit package URL lockfiles" in resp.json()["error"]


@pytest.mark.anyio
async def test_parse_endpoint_timeout(client, monkeypatch):
    monkeypatch.setattr("conda_presto.app.PARSE_TIMEOUT_S", 0.1)

    def slow_parse(*args, **kwargs):
        time.sleep(2)
        return None

    monkeypatch.setattr("conda_presto.app.ParsedInputFile.from_content", slow_parse)
    resp = await client.post(
        "/parse",
        json={
            "file": "dependencies:\n  - zlib\n",
            "filename": "environment.yml",
        },
    )
    assert resp.status_code == 504
    assert "timeout" in resp.json()["error"].lower()


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
