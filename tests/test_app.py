"""Tests for conda_presto.app (Litestar endpoints)."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import anyio
import conda_lockfiles.load_yaml as lockfile_yaml
import pytest
import yaml
from conda.models.environment import Environment
from conda.models.match_spec import MatchSpec
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
    capabilities,
    export_post,
    formats,
    health,
    openapi_json,
    parse,
    platforms,
    resolve_get,
    resolve_post,
    result_get,
    sbom_post,
    sign_post,
    solver_resources_lifespan,
    transcode_post,
    verify_post,
    version,
)
from conda_presto.cache import ResultCache
from conda_presto.exceptions import WorkspaceSolveError
from conda_presto.inputs import ParsedInputFile
from conda_presto.resolve import RepodataSnapshot, SolveResult
from conda_presto.storage import StoreOperationCoordinator
from conda_presto.workspace import WorkspaceInput, WorkspaceSolveResult


@pytest.fixture()
def test_app():
    app = Litestar(
        route_handlers=[
            openapi_json,
            resolve_get,
            resolve_post,
            export_post,
            transcode_post,
            sbom_post,
            sign_post,
            verify_post,
            capabilities,
            result_get,
            formats,
            platforms,
            version,
            parse,
            health,
        ],
        openapi_config=OpenAPIConfig(
            title="conda-presto",
            version="test",
            path="/schema",
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
def package_url_probe():
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
    package_url = (
        f"http://127.0.0.1:{server.server_address[1]}"
        "/conda-forge/linux-64/probe-1.0-h123_0.conda"
    )
    try:
        yield package_url, requests
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


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


def test_build_cors_config_disabled_without_origins():
    assert build_cors_config([]) is None


def test_http_logging_excludes_sensitive_request_data():
    logging_config = app_module.middleware[0].kwargs["config"]

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
async def test_concurrent_exports_keep_each_response_at_its_own_url(
    client, test_app, monkeypatch, fresh_repodata_snapshot
):
    entered = 0
    both_started = anyio.Event()

    async def run(*_args, **_kwargs):
        nonlocal entered
        entered += 1
        timestamp = entered
        if entered == 2:
            both_started.set()
        await both_started.wait()
        body = json.dumps({"metadata": {"timestamp": timestamp}}).encode()
        return body, "application/json"

    monkeypatch.setattr(app_module, "run_solve", run)
    monkeypatch.setattr(RepodataSnapshot, "capture", lambda *_: fresh_repodata_snapshot)
    responses = []

    async def resolve():
        response = await client.get("/resolve?spec=zlib&format=environment-json")
        responses.append(response)

    async with anyio.create_task_group() as group:
        group.start_soon(resolve)
        group.start_soon(resolve)

    assert len({response.headers["location"] for response in responses}) == 2
    for response in responses:
        retained = await client.get(response.headers["location"])
        assert retained.content == response.content
    request_key = next(
        key
        for key in test_app.state.result_cache.entries
        if key.startswith("request-v1:")
    )
    request_digest = request_key.removeprefix("request-v1:")
    assert (await client.get(f"/r/{request_digest}")).status_code == 404


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
    assert (
        resp.json()["error"]
        == "Result not in cache. Submit the solve again to recompute."
    )


@pytest.mark.anyio
async def test_result_cache_evicts_oldest_result(client, test_app, monkeypatch):
    test_app.state.result_cache = ResultCache(max_size=1)

    async def run(_request, specs, *_args, **_kwargs):
        return json.dumps({"package": specs[0]}).encode(), "application/json"

    monkeypatch.setattr(app_module, "run_solve", run)

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
        ResultCache.request_key(key),
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

    def fake_run_solve_in_process(channels, specs, platforms, format_name, deadline):
        captured["args"] = (
            channels,
            specs,
            platforms,
            format_name,
            deadline,
        )
        return []

    async def fake_run_sync(func, *args, abandon_on_cancel, limiter):
        captured["limiter"] = limiter
        captured["abandon_on_cancel"] = abandon_on_cancel
        return func(*args)

    monkeypatch.setattr(app_module, "run_solve_in_process", fake_run_solve_in_process)
    monkeypatch.setattr(app_module.anyio.to_thread, "run_sync", fake_run_sync)
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/resolve",
            json={"specs": ["zlib"], "platforms": ["linux-64"]},
        )

    assert response.status_code == 200
    assert captured["limiter"] is test_app.state.solver_limiter
    assert captured["abandon_on_cancel"] is False
    channels, specs, platforms, format_name, deadline = captured["args"]
    assert channels == ["conda-forge"]
    assert specs == ["zlib"]
    assert platforms == ["linux-64"]
    assert format_name is None
    assert deadline > time.monotonic()


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

    def fake_run_solve_in_process(channels, specs, platforms, format_name, deadline):
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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)
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
@pytest.mark.parametrize(
    ("source_format", "target_format", "request_kind", "expected_version"),
    [
        pytest.param(
            "pixi",
            "conda-lock-v1",
            "json",
            1,
            id="pixi-to-conda-lock",
        ),
        pytest.param(
            "conda-lock",
            "pixi-lock-v6",
            "raw",
            6,
            id="conda-lock-to-pixi",
        ),
    ],
)
async def test_transcode_post_lockfile_to_lockfile_without_fetching_or_solving(
    client,
    monkeypatch,
    package_url_probe,
    source_format,
    target_format,
    request_kind,
    expected_version,
):
    def fail_solve(*args, **kwargs):
        raise AssertionError("solver should not run")

    def fail_parent_export(*args, **kwargs):
        raise AssertionError("lockfile export should run in the parser process")

    parsed_results = []
    parse_input = app_module.parse_input_for_request

    async def record_parse_result(*args, **kwargs):
        parsed = await parse_input(*args, **kwargs)
        parsed_results.append(parsed)
        return parsed

    package_url, requests = package_url_probe
    monkeypatch.setattr(app_module, "solve_environments", fail_solve)
    monkeypatch.setattr(app_module.OutputFormat, "render", fail_parent_export)
    monkeypatch.setattr(app_module, "parse_input_for_request", record_parse_result)
    if source_format == "pixi":
        filename = "pixi.lock"
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
    sha256: {"a" * 64}
    md5: {"b" * 32}
    depends:
      - python >=3.13
"""
    else:
        filename = "conda-lock.yml"
        lockfile = f"""\
version: 1
metadata:
  channels: []
  platforms:
    - linux-64
package:
  - name: probe
    version: '1.0'
    manager: conda
    platform: linux-64
    dependencies:
      python: '>=3.13'
    url: {package_url}
    hash:
      sha256: {"a" * 64}
      md5: {"b" * 32}
"""

    url = f"/transcode?format={target_format}&platform=linux-64"
    if request_kind == "json":
        response = await client.post(
            url,
            json={
                "file": lockfile,
                "filename": filename,
                "platforms": ["linux-64"],
            },
        )
    else:
        response = await client.post(
            f"{url}&filename={filename}",
            content=lockfile,
            headers={"content-type": "application/yaml; charset=utf-8"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    assert response.headers["cache-control"] == "no-store"
    assert len(parsed_results) == 1
    assert parsed_results[0].environments == ()
    assert parsed_results[0].exported_content == response.text
    data = yaml.safe_load(response.text)
    assert data["version"] == expected_version
    if expected_version == 1:
        assert data["metadata"]["platforms"] == ["linux-64"]
        assert [package["name"] for package in data["package"]] == ["probe"]
        package = data["package"][0]
        assert package["url"] == package_url
        assert package["dependencies"] == {"python": ">=3.13"}
        assert package["hash"] == {"md5": "b" * 32, "sha256": "a" * 64}
    else:
        assert "linux-64" in data["environments"]["default"]["packages"]
        package = data["packages"][0]
        assert package["conda"] == package_url
        assert package["depends"] == ["python >=3.13"]
        assert package["md5"] == "b" * 32
        assert package["sha256"] == "a" * 64
    assert requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("package_entries", "expected_error"),
    [
        pytest.param(
            "  - conda: {package_url}\n    depends:\n      - '!!!'",
            "Invalid spec '!!!'",
            id="invalid-dependency",
        ),
        pytest.param(
            None,
            "missing from the packages list",
            id="dangling-reference",
        ),
    ],
)
async def test_transcode_post_rejects_invalid_lockfile_packages(
    client,
    package_url_probe,
    package_entries,
    expected_error,
):
    package_url, requests = package_url_probe
    packages = (
        "packages: []"
        if package_entries is None
        else "packages:\n" + package_entries.format(package_url=package_url)
    )
    lockfile = f"""\
version: 6
environments:
  default:
    channels: []
    packages:
      linux-64:
        - conda: {package_url}
{packages}
"""

    response = await client.post(
        "/transcode?format=conda-lock-v1&platform=linux-64",
        json={"file": lockfile, "filename": "pixi.lock"},
    )

    assert response.status_code == 400
    assert expected_error in response.json()["error"]
    assert requests == []


@pytest.mark.anyio
async def test_transcode_post_rejects_unrecoverable_conda_pypi_identity(client):
    wheel_url = (
        "https://files.pythonhosted.org/packages/ab/cd/docker-7.1.0-py3-none-any.whl"
    )
    lockfile = f"""\
version: 6
environments:
  default:
    channels:
      - url: conda-pypi
    packages:
      linux-64:
        - conda: {wheel_url}
packages:
  - conda: {wheel_url}
"""

    response = await client.post(
        "/transcode?format=conda-lock-v1&platform=linux-64",
        json={"file": lockfile, "filename": "pixi.lock"},
    )

    assert response.status_code == 400
    assert "without package metadata" in response.json()["error"]


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
    assert body["reasons"] == expected_reasons


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
    assert "/preflight" not in data["paths"]
    assert "/diff" not in data["paths"]
    assert "/explain" not in data["paths"]
    assert "/transcode" in data["paths"]
    assert "/export" in data["paths"]
    assert (
        data["paths"]["/export"]["post"]["operationId"]
        != data["paths"]["/transcode"]["post"]["operationId"]
    )
    assert "/parse" in data["paths"]
    assert "/r/{key}" in data["paths"]
    assert "/health" in data["paths"]
    assert "/solver/v1" not in data["paths"]
    assert not any(path.startswith("/ui/") for path in data["paths"])

    health_endpoint = data["paths"]["/health"]["get"]
    assert {"200", "503"} <= health_endpoint["responses"].keys()
    for status_code in ("200", "503"):
        assert health_endpoint["responses"][status_code]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/HealthResponse")

    parse_operation = data["paths"]["/parse"]["post"]
    assert parse_operation["requestBody"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ParseRequest")
    parse_schema = parse_operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert {entry["$ref"].rsplit("/", 1)[-1] for entry in parse_schema["oneOf"]} == {
        "ParseResult",
        "WorkspaceParseResult",
        "WorkspaceLockParseResult",
    }
    assert {"400", "504"} <= parse_operation["responses"].keys()


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

    def create_worker(channels, platforms):
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
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)
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
    assert set(data) == {"specs", "channels"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selectors,expected_pairs",
    [
        pytest.param({}, [], id="discovery"),
        pytest.param(
            {"environments": ["test"]},
            [("test", "linux-64"), ("test", "osx-arm64")],
            id="environment",
        ),
        pytest.param(
            {"platforms": ["osx-arm64"]},
            [("default", "osx-arm64"), ("test", "osx-arm64")],
            id="platform",
        ),
        pytest.param(
            {
                "environments": ["test", "default"],
                "platforms": ["osx-arm64", "linux-64"],
            },
            [
                ("test", "osx-arm64"),
                ("test", "linux-64"),
                ("default", "osx-arm64"),
                ("default", "linux-64"),
            ],
            id="ordered-selection",
        ),
    ],
)
async def test_parse_workspace_discovery_and_selection(
    client, workspace_manifest_text, selectors, expected_pairs
):
    response = await client.post(
        "/parse",
        json={"file": workspace_manifest_text, "filename": "conda.toml", **selectors},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "conda-toml"
    assert {env["name"] for env in body["environments"]} == {"default", "test"}
    assert [
        (row["environment"], row["platform"]) for row in body["selected"]
    ] == expected_pairs
    for row in body["selected"]:
        assert "zlib" in row["specs"]
        assert ("pytest" in row["specs"]) == (row["environment"] == "test")
        assert ("readline" in row["specs"]) == (row["platform"] == "linux-64")
        assert row["subdir"] == row["platform"]
    assert "manifest_path" not in response.text
    assert "[temporary-directory]" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selectors",
    [
        pytest.param({"environments": []}, id="empty-environments"),
        pytest.param({"platforms": []}, id="empty-platforms"),
        pytest.param({"environments": ["missing"]}, id="unknown-environment"),
        pytest.param({"platforms": ["win-64"]}, id="undeclared-platform"),
        pytest.param({"environments": "test"}, id="invalid-selector-type"),
    ],
)
async def test_parse_workspace_rejects_invalid_selection(
    client, workspace_manifest_text, selectors
):
    response = await client.post(
        "/parse",
        json={"file": workspace_manifest_text, "filename": "conda.toml", **selectors},
    )
    assert response.status_code == 400, response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content",
    ["tool = 42", "[tool]\nconda = 42", "[tool]\npixi = 42"],
    ids=["invalid-tool", "invalid-conda", "invalid-pixi"],
)
async def test_parse_workspace_rejects_malformed_pyproject_tables(client, content):
    response = await client.post(
        "/parse", json={"file": content, "filename": "pyproject.toml"}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]
    assert "Traceback" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selector", [{"environments": ["default"]}, {"platforms": ["linux-64"]}]
)
async def test_parse_rejects_workspace_selectors_for_ordinary_files(client, selector):
    response = await client.post(
        "/parse",
        json={
            "file": "dependencies: [zlib]",
            "filename": "environment.yml",
            **selector,
        },
    )
    assert response.status_code == 400
    assert "selection requires a workspace manifest" in response.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint,extra_specs", [("/resolve", ["zlib"]), ("/sbom", []), ("/sbom", ["zlib"])]
)
async def test_http_workspace_solve_is_rejected_before_cache(
    client, workspace_manifest_text, monkeypatch, endpoint, extra_specs
):
    async def unexpected_solve(*args, **kwargs):
        pytest.fail("Workspace input reached solve/cache")

    monkeypatch.setattr(app_module, "run_cached_solve", unexpected_solve)
    response = await client.post(
        endpoint,
        json={
            "file": workspace_manifest_text,
            "filename": "conda.toml",
            "platforms": ["linux-64"],
            "specs": extra_specs,
        },
    )
    assert response.status_code == 400
    assert "Workspace" in response.text


def test_spawned_workspace_parse_retains_full_config(workspace_manifest_text):
    parsed = ParsedInputFile.from_content_until(
        workspace_manifest_text,
        "conda.toml",
        ["linux-64"],
        time.monotonic() + 10,
        target_environments=["test"],
    )
    assert parsed.workspace.config._manifest_text == workspace_manifest_text
    assert set(parsed.workspace.config.environments) == {"default", "test"}
    assert (
        "readline"
        in parsed.workspace.config.features["default"].target_conda_dependencies[
            "linux-64"
        ]
    )
    assert parsed.specs == []
    assert parsed.channels == []


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [False, True], ids=["json", "raw-toml"])
async def test_workspace_solve_selectors_reach_worker(
    client, workspace_manifest_text, monkeypatch, raw
):
    calls = []

    async def record(
        request, specs, channels, platforms, format_name=None, workspace=None
    ):
        calls.append((workspace, specs, channels, platforms, format_name))
        return app_module.Response({"received": True})

    monkeypatch.setattr(app_module, "run_cached_solve", record)
    url = "/resolve?environment=test&platform=osx-arm64&format=conda-workspaces-lock-v1"
    if raw:
        response = await client.post(
            url + "&filename=conda.toml",
            content=workspace_manifest_text,
            headers={"Content-Type": "application/toml"},
        )
        expected = [("test", "osx-arm64")]
    else:
        response = await client.post(
            url,
            json={
                "file": workspace_manifest_text,
                "filename": "conda.toml",
                "environments": ["default"],
                "platforms": ["linux-64"],
            },
        )
        expected = [("default", "linux-64")]
    assert response.status_code == 200, response.text
    workspace, specs, channels, platforms, format_name = calls[0]
    assert [
        (target.environment, target.platform) for target in workspace.result.selected
    ] == expected
    assert "zlib" in specs
    assert channels == ["https://conda.anaconda.org/conda-forge"]
    assert platforms == [expected[0][1]]
    assert format_name == "conda-workspaces-lock-v1"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fields,format_name",
    [
        ({"environments": []}, None),
        ({"platforms": []}, None),
        ({"environments": ["missing"]}, None),
        ({"channels": ["conda-forge"]}, None),
        ({}, "conda-toml"),
    ],
    ids=[
        "empty-environments",
        "empty-platforms",
        "unknown-environment",
        "override-channels",
        "multiple-environments-export",
    ],
)
async def test_workspace_solve_rejects_selection_before_worker(
    client, workspace_manifest_text, monkeypatch, fields, format_name
):
    def unexpected_solve(*args, **kwargs):
        pytest.fail("Invalid selection reached the solver")

    monkeypatch.setattr(WorkspaceInput, "solve", unexpected_solve)
    response = await client.post(
        "/resolve" + (f"?format={format_name}" if format_name else ""),
        json={"file": workspace_manifest_text, "filename": "conda.toml", **fields},
    )
    assert response.status_code == 400, response.text


@pytest.mark.anyio
@pytest.mark.parametrize("format_name", [None, "conda-workspaces-lock-v1"])
async def test_workspace_solve_retains_exact_output_and_uses_matrix_cache(
    client, workspace_manifest_text, monkeypatch, format_name
):
    calls = []
    snapshot_options = []
    snapshot = RepodataSnapshot(
        (("https://example.org", "repodata.json", 1, 1),), False
    )

    def capture(*args, **kwargs):
        snapshot_options.append(kwargs)
        return snapshot

    monkeypatch.setattr(RepodataSnapshot, "capture", capture)
    monkeypatch.setattr(
        WorkspaceInput, "repodata_options", staticmethod(lambda: {"use_shards": True})
    )

    def solve(self, format_name=None):
        calls.append(self.result.selected)
        if format_name:
            return "version: 1\nenvironments: {}\npackages: []\n", "application/yaml"
        return [
            WorkspaceSolveResult(t.environment, t.platform, t.subdir, [])
            for t in self.result.selected
        ]

    monkeypatch.setattr(WorkspaceInput, "solve", solve)
    url = "/resolve" + (f"?format={format_name}" if format_name else "")
    body = {"file": workspace_manifest_text, "filename": "conda.toml"}
    first = await client.post(url, json=body)
    second = await client.post(url, json=body)
    assert first.status_code == second.status_code == 200, first.text
    assert len(calls) == 1
    assert len(calls[0]) == 4
    retained = await client.get(first.headers["location"])
    assert retained.content == second.content == first.content
    third = await client.post(url, json={**body, "environments": ["test"]})
    assert third.status_code == 200, third.text
    assert len(calls) == 2
    assert snapshot_options
    assert all(options.get("use_shards") is True for options in snapshot_options)


@pytest.mark.anyio
@pytest.mark.parametrize("use_shards", [False, True], ids=["json", "shards"])
async def test_workspace_cache_tracks_only_selected_channel_platform_pairs(
    client, monkeypatch, tmp_path, use_shards
):
    manifest = """\
[workspace]
platforms = ["linux-64", "osx-arm64"]
[dependencies]
zlib = "*"
[feature.linux]
platforms = ["linux-64"]
channels = ["https://a.example.org/channel"]
[feature.osx]
platforms = ["osx-arm64"]
channels = ["https://b.example.org/channel"]
[feature.osx.dependencies]
zlib = { version = "*", channel = "https://c.example.org/channel" }
[environments.a]
features = ["linux"]
[environments.b]
features = ["osx"]
"""
    fresh_urls = {
        f"https://{host}.example.org/channel/{subdir}"
        for host, platform in (
            ("a", "linux-64"),
            ("b", "osx-arm64"),
            ("c", "osx-arm64"),
        )
        for subdir in (platform, "noarch")
    }
    repodata = tmp_path / "repodata.json"
    repodata.write_text("{}")
    shards = tmp_path / "repodata.msgpack.zst"
    shards.write_bytes(b"shards")
    declared = tmp_path / "declared.json"
    declared.write_text("{}")
    missing = tmp_path / "missing"
    calls = []

    def subdir_data(channel, **kwargs):
        present = channel.url() in fresh_urls
        dependency_channel = channel.location == "c.example.org"
        return SimpleNamespace(
            repo_cache=SimpleNamespace(
                cache_path_json=(repodata if dependency_channel else declared)
                if present
                else missing,
                cache_path_shards=(shards if dependency_channel else declared)
                if present
                else missing,
                state=SimpleNamespace(should_check_format=lambda _: use_shards),
                load_state=lambda **_: None,
                stale=lambda: False,
            )
        )

    def solve(self, format_name=None):
        calls.append(self.result.selected)
        return [
            WorkspaceSolveResult(t.environment, t.platform, t.subdir, [])
            for t in self.result.selected
        ]

    monkeypatch.setattr("conda_presto.resolve.SubdirData", subdir_data)
    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", ["*"])
    monkeypatch.setattr(
        WorkspaceInput,
        "repodata_options",
        staticmethod(lambda: {"use_shards": use_shards}),
    )
    monkeypatch.setattr(WorkspaceInput, "solve", solve)
    body = {
        "file": manifest,
        "filename": "conda.toml",
        "environments": ["a", "b"],
    }
    first = await client.post("/resolve", json=body)
    second = await client.post("/resolve", json=body)
    assert first.status_code == second.status_code == 200, first.text
    assert len(calls) == 1
    retained = await client.get(first.headers["location"])
    assert retained.content == second.content == first.content

    source = shards if use_shards else repodata
    source.write_bytes(b"updated metadata")
    third = await client.post("/resolve", json=body)
    assert third.status_code == 200, third.text
    assert len(calls) == 2


@pytest.mark.anyio
async def test_workspace_export_failure_identifies_pair_without_retention(
    client, workspace_manifest_text, monkeypatch
):
    def fail(self, format_name=None):
        raise WorkspaceSolveError("test", "osx-arm64", "Packages unavailable")

    monkeypatch.setattr(WorkspaceInput, "solve", fail)
    response = await client.post(
        "/resolve?format=conda-workspaces-lock-v1",
        json={"file": workspace_manifest_text, "filename": "conda.toml"},
    )
    assert response.status_code == 500, response.text
    assert response.json() == {
        "error": "Packages unavailable",
        "environment": "test",
        "platform": "osx-arm64",
    }
    assert "location" not in response.headers


@pytest.mark.anyio
async def test_parse_endpoint_accepts_requirements_file(client, test_app):
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)

    response = await client.post(
        "/parse",
        json={"file": "# packages\nzlib\n\n*\n*foo\n", "filename": "arbitrary.txt"},
    )

    assert response.status_code == 200
    assert response.json()["specs"] == ["zlib", "*", "*foo"]
    assert response.json()["channels"] == []


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["/parse", "/resolve"])
async def test_http_rejects_yaml_uploaded_as_text(client, test_app, endpoint):
    test_app.state.solver_limiter = anyio.CapacityLimiter(1)
    content = "base: &base\n  package: zlib\ncopy:\n  <<: *base\n"
    if endpoint == "/parse":
        response = await client.post(
            endpoint,
            json={"file": content, "filename": "environment.txt"},
        )
    else:
        response = await client.post(
            endpoint,
            content=content,
            headers={"Content-Type": "text/plain"},
        )

    assert response.status_code == 400


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
async def test_resolve_lockfile_does_not_fetch_package_urls(client, package_url_probe):
    package_url, requests = package_url_probe
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

    response = await client.post(
        "/resolve",
        json={
            "file": lockfile,
            "filename": "pixi.lock",
            "platforms": ["linux-64"],
        },
    )

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
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        pytest.param("environment.yml", "dependencies:\n  - zlib\n", id="yaml"),
        pytest.param("pixi.toml", '[dependencies]\nzlib = "*"\n', id="toml"),
    ],
)
def test_input_parse_process_entrypoint_sends_sanitized_result(
    monkeypatch,
    tmp_path,
    outcome,
    status,
    filename,
    content,
):
    path = tmp_path / filename
    path.write_text(content)
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
            "arbitrary.toml",
            'x = """a: &a {package: zlib}\nb: *a\n#"""\n',
            10,
            "YAML aliases are not accepted",
            id="toml-with-yaml-alias",
        ),
        pytest.param(
            "arbitrary.toml",
            'x = """a: [a, b, c]\n#"""\n',
            3,
            "structural complexity limit",
            id="toml-with-yaml-nodes",
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


@pytest.mark.parametrize("filename", ["environment.txt", "arbitrary.TXT"])
@pytest.mark.parametrize(
    ("content", "expected_status"),
    [
        pytest.param("zlib\n*\n*foo\n", "ok", id="requirements"),
        pytest.param(
            "base: &base\n  package: zlib\ncopy:\n  <<: *base\n",
            "invalid",
            id="yaml-merge-alias",
        ),
    ],
)
def test_http_text_parser_does_not_load_yaml(
    monkeypatch, tmp_path, filename, content, expected_status
):
    path = tmp_path / filename
    path.write_text(content)
    sent = []
    yaml_calls = []
    sender = SimpleNamespace(send=sent.append, close=lambda: None)

    def reject_yaml(source):
        yaml_calls.append(source)
        raise AssertionError("Text uploads must not reach a YAML loader")

    monkeypatch.setattr(inputs_module.os, "environ", {})
    monkeypatch.setattr(lockfile_yaml, "yaml_safe_load", reject_yaml)

    ParsedInputFile._from_path_process(
        sender,
        path,
        None,
        time.monotonic() + 10,
    )

    assert yaml_calls == []
    status, payload = sent[0]
    assert status == expected_status
    if status == "ok":
        assert payload.specs == ["zlib", "*", "*foo"]


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


@pytest.fixture()
def inline_attestation(monkeypatch):
    def run_until(service, operation, *, deadline, **kwargs):
        if operation == "verify":
            kwargs["bundle"] = kwargs.pop("bundle_json")
        return getattr(service, operation)(**kwargs)

    monkeypatch.setattr(app_module.AttestationService, "run_until", run_until)


@pytest.mark.anyio
@pytest.mark.parametrize("platforms", [["linux-64"], ["linux-64", "osx-arm64"]])
async def test_sbom_returns_separate_exact_documents(
    client, monkeypatch, make_package_record, platforms, fresh_repodata_snapshot
):
    monkeypatch.setattr(RepodataSnapshot, "capture", lambda *_: fresh_repodata_snapshot)

    def environments(channels, specs, selected):
        assert specs == ["zlib"]
        return [
            Environment(
                platform=platform,
                requested_packages=[MatchSpec("zlib")],
                explicit_packages=[
                    make_package_record(
                        subdir=platform, depends=(), sha256="a" * 64, md5="b" * 32
                    )
                ],
            )
            for platform in selected
        ]

    monkeypatch.setattr(app_module, "solve_environments", environments)
    response = await client.post(
        "/sbom", json={"specs": ["zlib"], "platforms": platforms}
    )
    assert response.status_code == 200, response.text
    documents = response.json()["sboms"]
    assert [item["platform"] for item in documents] == platforms
    for item in documents:
        body = item["content"].encode()
        document = json.loads(body)
        assert document["bomFormat"] == "CycloneDX"
        assert document["specVersion"] == "1.7"
        assert document["components"][0]["name"] == "zlib"
        assert "dependencies" not in document["compositions"][0]
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
        retained = await client.get(item["location"])
        assert retained.content == body


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["missing-provider", "no-platform", "solve-failed"])
async def test_sbom_rejects_unavailable_or_incomplete_generation(
    client, monkeypatch, case
):
    if case == "missing-provider":
        monkeypatch.setattr(app_module.OutputFormat, "available", lambda: [])
    if case == "solve-failed":

        async def fail(*_args, **_kwargs):
            return app_module.Response({"error": "Solve failed"}, status_code=500)

        monkeypatch.setattr(app_module, "run_cached_solve", fail)
    response = await client.post(
        "/sbom",
        json={
            "specs": ["zlib"],
            "platforms": [] if case == "no-platform" else ["linux-64"],
        },
    )
    assert (
        response.status_code
        == {"missing-provider": 503, "no-platform": 400, "solve-failed": 500}[case]
    )
    assert "sboms" not in response.json()


@pytest.mark.anyio
async def test_sign_binds_retained_bytes_and_rejects_caller_claims(
    client, test_app, monkeypatch, inline_attestation
):
    body = b"@EXPLICIT\nhttps://example.test/zlib.conda\n"
    monkeypatch.setattr(app_module, "SIGSTORE_SIGNING_ENABLED", True)
    retained = await test_app.state.result_cache.remember(
        "request-v1:sign-example", body, "text/plain"
    )
    key = retained.headers["Location"].rsplit("/", 1)[-1]
    calls = []

    def sign(_self, body, *, artifact_name):
        calls.append((body, artifact_name))
        return '{"bundle": "test"}'

    monkeypatch.setattr(app_module.AttestationService, "sign", sign)
    response = await client.post("/sign", json={"key": key})
    assert response.status_code == 200, response.text
    assert calls == [(body, f"result-{key}")]
    assert response.json()["sha256"] == hashlib.sha256(body).hexdigest()
    assert response.json()["sha256"] != key
    assert response.json()["bundle"] == '{"bundle": "test"}'
    assert (await client.get(f"/r/{key}")).content == body
    rejected = await client.post("/sign", json={"key": key, "statement": {}})
    assert rejected.status_code == 400
    assert len(calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["disabled", "missing", "invalid", "no-credential"])
async def test_sign_fails_without_eligible_output_or_credentials(
    client, test_app, monkeypatch, case, inline_attestation
):
    key = "b" * 64
    monkeypatch.setattr(app_module, "SIGSTORE_SIGNING_ENABLED", case != "disabled")
    if case == "no-credential":
        retained = await test_app.state.result_cache.remember(
            "request-v1:sign-example", b"result", "text/plain"
        )
        key = retained.headers["Location"].rsplit("/", 1)[-1]

        def fail(*_args, **_kwargs):
            raise app_module.AttestationError(
                "signing-credentials-unavailable", "Unavailable"
            )

        monkeypatch.setattr(app_module.AttestationService, "sign", fail)
    response = await client.post(
        "/sign", json={"key": "invalid" if case == "invalid" else key}
    )
    assert (
        response.status_code
        == {"disabled": 503, "missing": 404, "invalid": 400, "no-credential": 503}[case]
    )
    assert "bundle" not in response.json()


@pytest.mark.anyio
async def test_verify_decodes_exact_bytes_and_passes_recipient_identity(
    client, monkeypatch, inline_attestation
):
    artifact = b"\x00\xff\r\nartifact"
    calls = []

    def verify(_self, body, bundle, **kwargs):
        calls.append((body, bundle, kwargs))
        return {"signature_verified": True, "claims_checked": False}

    monkeypatch.setattr(app_module.AttestationService, "verify", verify)
    response = await client.post(
        "/verify",
        json={
            "artifact": base64.b64encode(artifact).decode(),
            "bundle": "{}",
            "artifact_name": "saved-output",
            "expected_identity": "workflow",
            "expected_issuer": "https://issuer.example",
        },
    )
    assert response.status_code == 200, response.text
    assert calls == [
        (
            artifact,
            "{}",
            {
                "artifact_name": "saved-output",
                "expected_identity": "workflow",
                "expected_issuer": "https://issuer.example",
            },
        )
    ]
    assert response.json()["claims_checked"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code,status",
    [
        ("artifact-mismatch", 422),
        ("untrusted-identity", 422),
        ("evidence-unavailable", 503),
        ("verification-failed", 500),
        ("operation-failed", 500),
        ("provider-unavailable", 503),
    ],
)
async def test_verify_distinguishes_rejected_evidence_from_unavailable_provider(
    client, monkeypatch, code, status, inline_attestation
):
    def fail(*_args, **_kwargs):
        raise app_module.AttestationError(code, "Verification unavailable or rejected")

    monkeypatch.setattr(app_module.AttestationService, "verify", fail)
    response = await client.post(
        "/verify",
        json={
            "artifact": "YQ==",
            "bundle": "{}",
            "artifact_name": "a",
            "expected_identity": "workflow",
            "expected_issuer": "https://issuer.example",
        },
    )
    assert response.status_code == status
    assert response.json()["code"] == code


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/", "/openapi.json"])
async def test_root_serves_openapi_without_browser_routes(client, path):
    response = await client.get(path)
    assert response.status_code == 200
    assert response.json()["openapi"].startswith("3.")
    for retired in (
        "/preflight",
        "/repair",
        "/diff",
        "/explain",
        "/solver/v1",
        "/ui/resolve",
    ):
        assert retired not in response.json()["paths"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "installed,enabled,offline",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, True, True),
    ],
)
async def test_capabilities_separates_provider_and_signing_configuration(
    client, monkeypatch, installed, enabled, offline
):
    monkeypatch.setattr(app_module.AttestationService, "available", lambda: installed)
    monkeypatch.setattr(app_module, "SIGSTORE_SIGNING_ENABLED", enabled)
    monkeypatch.setattr(app_module, "SIGSTORE_ALLOW_PUBLIC_SIGNING", True)
    monkeypatch.setattr(app_module, "SIGSTORE_OFFLINE", offline)
    response = await client.get("/capabilities")
    assert response.json() == {
        "workspace_parse": True,
        "workspace_solve": True,
        "workspace_lock_parse": True,
        "workspace_lock_export": True,
        "export": True,
        "sbom": True,
        "verify": installed,
        "sign": installed and enabled and not offline,
    }


@pytest.mark.anyio
async def test_sbom_rejects_lockfile_inspection(client, pixi_lock_v6_text):
    response = await client.post(
        "/sbom",
        json={
            "file": pixi_lock_v6_text,
            "filename": "pixi.lock",
            "platforms": ["linux-64"],
        },
    )
    assert response.status_code == 400
    assert "inspection is not supported" in response.json()["error"]


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["sign", "verify", "sbom"])
async def test_artifact_operation_timeout_returns_no_partial_success(
    client, test_app, monkeypatch, operation
):
    def timeout(*_args, **_kwargs):
        raise TimeoutError

    async def solve_timeout(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(app_module.AttestationService, "run_until", timeout)
    monkeypatch.setattr(app_module, "run_cached_solve", solve_timeout)
    monkeypatch.setattr(app_module, "SIGSTORE_SIGNING_ENABLED", True)
    if operation == "sign":
        response = await test_app.state.result_cache.remember(
            "request-v1:test", b"result", "text/plain"
        )
        data = {"key": response.headers["Location"].rsplit("/", 1)[-1]}
    elif operation == "verify":
        data = {
            "artifact": "YQ==",
            "bundle": "{}",
            "artifact_name": "a",
            "expected_identity": "workflow",
            "expected_issuer": "https://issuer.example",
        }
    else:
        data = {"specs": ["zlib"], "platforms": ["linux-64"]}
    response = await client.post(f"/{operation}", json=data)
    assert response.status_code == 504
    assert "error" in response.json()


@pytest.mark.anyio
async def test_sbom_solves_and_exports_requested_roots_with_installed_provider(client):
    response = await client.post(
        "/sbom", json={"specs": ["zlib"], "platforms": ["linux-64"]}
    )
    assert response.status_code == 200, response.text
    item = response.json()["sboms"][0]
    document = json.loads(item["content"])
    root = document["metadata"]["component"]["bom-ref"]
    zlib = next(
        component for component in document["components"] if component["name"] == "zlib"
    )
    dependencies = next(
        edge for edge in document["dependencies"] if edge["ref"] == root
    )
    assert dependencies["dependsOn"] == [zlib["bom-ref"]]
    assert item["sha256"] == hashlib.sha256(item["content"].encode()).hexdigest()
