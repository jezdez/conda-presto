"""Workspace dependency channels obey HTTP policy and retain their identity."""

from __future__ import annotations

import json
import threading
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conda.base.context import context
from httpx import ASGITransport, AsyncClient
from litestar import Litestar

import conda_presto.app as app_module
from conda_presto.cache import ResultCache
from conda_presto.workspace import WorkspaceInput


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def workspace_client():
    app = Litestar(route_handlers=[app_module.resolve_post, app_module.parse])
    app.state.solver_limiter = None
    app.state.result_cache = ResultCache(max_size=8)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def channel_manifest(tmp_path):
    def create(channel, *, declared="conda-forge", dependencies=None):
        content = (
            "[workspace]\n"
            f"channels = [{json.dumps(declared)}]\n"
            'platforms = ["linux-64"]\n'
            'channel-priority = "strict"\n'
            "[dependencies]\n"
        )
        content += (
            f'probe = {{ version = "*", channel = {json.dumps(channel)} }}\n'
            if channel is not None
            else 'probe = "*"\n'
        )
        if dependencies is not None:
            content += f"[pypi-dependencies]\n{dependencies}\n"
        path = tmp_path / "conda.toml"
        path.write_text(content, encoding="utf-8")
        return path

    return create


@pytest.fixture
def channel_repodata_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if not self.path.endswith("/repodata.json"):
                self.send_error(404)
                return
            subdir = self.path.split("/")[-2]
            packages = {}
            if self.path == "/blocked/linux-64/repodata.json":
                packages["probe-1.0-0.tar.bz2"] = {
                    "name": "probe",
                    "version": "1.0",
                    "build": "0",
                    "build_number": 0,
                    "subdir": "linux-64",
                    "depends": [],
                    "md5": "a" * 32,
                    "sha256": "b" * 64,
                    "size": 1,
                }
            payload = json.dumps(
                {
                    "info": {"subdir": subdir},
                    "packages": packages,
                    "packages.conda": {},
                    "repodata_version": 1,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def local_repodata_context(tmp_path):
    settings = {
        "_pkgs_dirs": (str(tmp_path / "packages"),),
        "repodata_use_shards": False,
        "repodata_use_zst": False,
        "repodata_fns": ("repodata.json",),
        "offline": False,
        "use_index_cache": False,
        "no_lock": True,
        "auto_update_conda": False,
        "_aggressive_update_packages": (),
    }
    with ExitStack() as stack:
        for name, value in settings.items():
            stack.enter_context(context._override(name, value))
        yield


@pytest.mark.anyio
@pytest.mark.parametrize(
    "dependency_allowed", [False, True], ids=["blocked", "allowed"]
)
async def test_workspace_dependency_channel_is_checked_before_fetching(
    workspace_client,
    channel_manifest,
    channel_repodata_server,
    local_repodata_context,
    monkeypatch,
    dependency_allowed,
):
    base, requests = channel_repodata_server
    declared, dependency = f"{base}/allowed", f"{base}/blocked"
    path = channel_manifest(dependency, declared=declared)
    monkeypatch.setattr(
        app_module,
        "CHANNEL_ALLOWLIST",
        [declared, dependency] if dependency_allowed else [declared],
    )
    response = await workspace_client.post(
        "/resolve",
        json={"file": path.read_text(), "filename": "conda.toml"},
    )

    if not dependency_allowed:
        assert response.status_code == 400, response.text
        assert response.json() == {"error": "Unsupported channel(s)"}
        assert requests == []
    else:
        assert response.status_code == 200, response.text
        assert "/blocked/linux-64/repodata.json" in requests
        assert "/blocked/noarch/repodata.json" in requests


@pytest.mark.anyio
async def test_workspace_declared_channel_solves_local_package(
    workspace_client,
    channel_manifest,
    channel_repodata_server,
    local_repodata_context,
    monkeypatch,
):
    base, requests = channel_repodata_server
    channel = f"{base}/blocked"
    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", [channel])
    path = channel_manifest(None, declared=channel)
    response = await workspace_client.post(
        "/resolve",
        json={"file": path.read_text(), "filename": "conda.toml"},
    )
    assert response.status_code == 200, response.text
    result = response.json()[0]
    assert result["error"] is None, response.text
    assert [package["name"] for package in result["packages"]] == ["probe"]
    assert result["packages"][0]["url"].startswith(channel + "/linux-64/")
    assert "/blocked/linux-64/repodata.json" in requests


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["/resolve", "/parse"])
@pytest.mark.parametrize(
    "channel,allowlist",
    [
        pytest.param("other", ["conda-forge"], id="named"),
        pytest.param(
            "https://elsewhere.example.test/conda-forge",
            ["conda-forge"],
            id="same-name-other-host",
        ),
        pytest.param(
            "http://conda.anaconda.org/conda-forge",
            ["conda-forge"],
            id="scheme-downgrade",
        ),
        pytest.param("file:///tmp/channel", ["*"], id="wildcard-file"),
    ],
)
async def test_workspace_dependency_channel_is_rejected_before_cache(
    workspace_client, channel_manifest, monkeypatch, endpoint, channel, allowlist
):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Disallowed workspace channels must not reach the cache")

    monkeypatch.setattr(app_module, "CHANNEL_ALLOWLIST", allowlist)
    monkeypatch.setattr(app_module, "run_cached_solve", forbidden)
    path = channel_manifest(channel)
    response = await workspace_client.post(
        endpoint,
        json={
            "file": path.read_text(),
            "filename": "conda.toml",
            "environments": ["default"],
        },
    )
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "Unsupported channel(s)"}


@pytest.mark.parametrize(
    "channel",
    [
        "https://elsewhere.example.test/team",
        "file:///tmp/channel",
    ],
    ids=["remote", "local"],
)
def test_workspace_effective_channels_preserve_sources_and_declared_order(
    channel_manifest, channel
):
    parsed = WorkspaceInput.from_path(
        channel_manifest(channel), environments=["default"]
    )
    target = parsed.result.selected[0]
    assert target.channels == ["conda-forge"]
    assert target.channel_priority == "strict"
    effective = parsed.solve_channels(target)
    assert effective[-1] == channel
    assert len(effective) == 2
    assert target.channels == ["conda-forge"]


def test_workspace_cache_identity_distinguishes_dependency_channel_hosts(
    channel_manifest,
):
    workspaces = [
        WorkspaceInput.from_path(
            channel_manifest(f"https://{host}.example.test/team"),
            environments=["default"],
        )
        for host in ("first", "second")
    ]
    assert workspaces[0].result.selected == workspaces[1].result.selected
    assert workspaces[0].cache_identity() != workspaces[1].cache_identity()


@pytest.mark.parametrize(
    "declaration",
    [
        'requests = "[channel=other]"',
        'requests = { extras = ["channel=other"] }',
        '"requests[channel=other]" = "*"',
        'requests = " @ https://example.test/requests.whl"',
        'requests = "===abc[channel=other]"',
        'requests = "===x::probe"',
    ],
    ids=[
        "version-channel",
        "extras-channel",
        "name-channel",
        "version-url",
        "arbitrary-equality-channel",
        "arbitrary-equality-channel-prefix",
    ],
)
def test_workspace_rejects_pypi_fields_that_introduce_matchspec_sources(
    channel_manifest, declaration
):
    with pytest.raises(ValueError):
        WorkspaceInput.from_path(
            channel_manifest("conda-forge", dependencies=declaration),
            environments=["default"],
        )


@pytest.mark.parametrize("version", ["*", ">=2,<3", "===foo"])
def test_workspace_preserves_valid_pypi_requirements(channel_manifest, version):
    parsed = WorkspaceInput.from_path(
        channel_manifest(
            "conda-forge",
            dependencies=(
                f'requests = {{ version = "{version}", extras = ["socks"] }}'
            ),
        ),
        environments=["default"],
    )
    assert parsed.result.selected[0].pypi_dependencies == {
        "requests": {"version": version, "extras": ["socks"]}
    }
