"""HTTP discovery and exact export of named locked environments."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from litestar import Litestar

import conda_presto.app as app_module
from conda_presto.app import (
    capabilities,
    parse,
    resolve_post,
    result_get,
    sbom_post,
    transcode_post,
)
from conda_presto.cache import ResultCache


@pytest.fixture()
async def lock_client(monkeypatch):
    def unexpected_work(*args, **kwargs):
        pytest.fail("Lock export must not solve, capture repodata or render in HTTP")

    monkeypatch.setattr(app_module, "solve", unexpected_work)
    monkeypatch.setattr(app_module, "solve_environments", unexpected_work)
    monkeypatch.setattr(app_module.RepodataSnapshot, "capture", unexpected_work)
    monkeypatch.setattr(app_module.OutputFormat, "render", unexpected_work)
    app = Litestar(
        route_handlers=[
            parse,
            transcode_post,
            resolve_post,
            result_get,
            sbom_post,
            capabilities,
        ]
    )
    app.state.solver_limiter = None
    app.state.result_cache = ResultCache(max_size=256)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture()
def lock_document():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_error(500, "Lock export must not download packages")

        do_HEAD = do_GET

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    channel = f"http://127.0.0.1:{server.server_port}/channel"
    packages = [
        {
            "conda": f"{channel}/{platform}/probe-1.0-h123_0.conda",
            "name": "probe",
            "version": "1.0",
            "build": "h123_0",
            "build_number": 0,
            "subdir": platform,
            "sha256": digest * 64,
            "md5": digest * 32,
            "depends": ["python >=3.13"],
            "size": 1234,
            "license": "BSD-3-Clause",
        }
        for platform, digest in (("linux-64", "a"), ("osx-arm64", "b"))
    ]
    linux, mac = ({"conda": package["conda"]} for package in packages)
    document = {
        "version": 1,
        "environments": {
            "default": {
                "channels": [{"url": channel}],
                "packages": {"linux-cuda": [linux], "osx-arm64": [mac]},
            },
            "test": {
                "channels": [{"url": channel}],
                "packages": {"linux-64": [linux.copy()]},
            },
        },
        "packages": packages,
    }
    try:
        yield document
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        assert requests == []


@pytest.mark.anyio
async def test_parse_discovers_named_lock_targets(lock_client, lock_document):
    response = await lock_client.post(
        "/parse", json={"file": yaml.safe_dump(lock_document), "filename": "conda.lock"}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "format": "conda-workspaces-lock-v1",
        "environments": [
            {
                "name": "default",
                "platforms": {"linux-cuda": "linux-64", "osx-arm64": "osx-arm64"},
            },
            {"name": "test", "platforms": {"linux-64": "linux-64"}},
        ],
        "selected": [],
    }
    selected = await lock_client.post(
        "/parse",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-cuda"],
        },
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selected"] == [
        {"environment": "default", "platform": "linux-cuda", "subdir": "linux-64"}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "inline_inputs",
    [
        pytest.param({}, id="lock-only"),
        pytest.param({"specs": ["python"]}, id="extra-specs"),
        pytest.param(
            {"specs": ["python"], "channels": ["conda-forge"]},
            id="specs-and-channels",
        ),
    ],
)
async def test_resolve_rejects_workspace_locks_before_solver(
    lock_client, lock_document, inline_inputs
):
    response = await lock_client.post(
        "/resolve",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            **inline_inputs,
        },
    )

    assert response.status_code == 400, response.text
    assert response.json() == {
        "error": (
            "Workspace lockfiles cannot be solved. "
            "Use POST /export to export their exact locked records."
        )
    }
    assert "location" not in response.headers


@pytest.mark.anyio
async def test_sbom_preserves_unsupported_locked_input_error(
    lock_client, lock_document, monkeypatch
):
    monkeypatch.setattr(
        app_module.OutputFormat, "available", lambda: ["cyclonedx-json-v1.7"]
    )
    response = await lock_client.post(
        "/sbom",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == 400, response.text
    assert response.json() == {
        "error": (
            "SBOM requests solve requirements. "
            "Resolved lockfile inspection is not supported."
        )
    }


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["transcode", "export"])
async def test_lock_extraction_retains_source_records_for_raw_and_json(
    lock_client, lock_document, endpoint
):
    content = yaml.safe_dump(lock_document)
    url = f"/{endpoint}?format=conda-workspaces-lock-v1"
    json_response = await lock_client.post(
        url,
        json={
            "file": content,
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-cuda"],
        },
    )
    raw_response = await lock_client.post(
        f"{url}&filename=conda.lock&environment=default&platform=linux-cuda",
        content=content,
        headers={"content-type": "application/yaml"},
    )

    assert json_response.status_code == 200, json_response.text
    assert raw_response.status_code == 200, raw_response.text
    assert json_response.content == raw_response.content
    result = yaml.safe_load(json_response.content)
    assert result == {
        "version": 1,
        "environments": {
            "default": {
                "channels": lock_document["environments"]["default"]["channels"],
                "packages": {
                    "linux-cuda": lock_document["environments"]["default"]["packages"][
                        "linux-cuda"
                    ]
                },
            }
        },
        "packages": [lock_document["packages"][0]],
    }
    retained = await lock_client.get(json_response.headers["location"])
    assert retained.status_code == 200
    assert retained.content == json_response.content
    assert retained.headers["content-type"] == json_response.headers["content-type"]


@pytest.mark.anyio
async def test_lock_extraction_defaults_to_all_targets(lock_client, lock_document):
    response = await lock_client.post(
        "/transcode?format=workspace-lock",
        json={"file": yaml.safe_dump(lock_document), "filename": "conda.lock"},
    )

    assert response.status_code == 200, response.text
    assert yaml.safe_load(response.content) == lock_document


@pytest.mark.anyio
async def test_export_writes_exact_explicit_records(lock_client, lock_document):
    response = await lock_client.post(
        "/export?format=explicit",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-cuda"],
        },
    )

    assert response.status_code == 200, response.text
    assert "@EXPLICIT" in response.text
    assert [line for line in response.text.splitlines() if line.startswith("http")] == [
        lock_document["packages"][0]["conda"]
    ]
    retained = await lock_client.get(response.headers["location"])
    assert retained.content == response.content


@pytest.mark.anyio
async def test_export_registered_environment_format_from_locked_records(
    lock_client, lock_document
):
    response = await lock_client.post(
        "/export?format=environment-yaml",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "environments": ["test"],
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == 200, response.text
    assert yaml.safe_load(response.content)["dependencies"] == ["probe=1.0=h123_0"]
    retained = await lock_client.get(response.headers["location"])
    assert retained.content == response.content


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "format_name", "selection", "error"),
    [
        ("parse", None, {"environments": ["absent"]}, "absent"),
        ("parse", None, {"environments": []}, "at least one"),
        (
            "parse",
            None,
            {"environments": ["test"], "platforms": ["linux-cuda"]},
            "linux-cuda",
        ),
        ("export", "explicit", {}, "environment"),
        (
            "export",
            "explicit",
            {"environments": ["default"]},
            "target",
        ),
        (
            "transcode",
            "environment-yaml",
            {"environments": ["test"]},
            "output format is not a lockfile",
        ),
    ],
)
async def test_lock_selection_and_output_errors(
    lock_client, lock_document, endpoint, format_name, selection, error
):
    response = await lock_client.post(
        f"/{endpoint}" + (f"?format={format_name}" if format_name else ""),
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            **selection,
        },
    )

    assert response.status_code == 400, response.text
    assert error in response.text
    assert "location" not in response.headers


@pytest.mark.anyio
async def test_lock_platform_alias_rejects_ambiguous_logical_targets(
    lock_client, workspace_lock_text
):
    response = await lock_client.post(
        "/export?format=explicit",
        json={
            "file": workspace_lock_text,
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == 400, response.text
    assert "ambiguous" in response.text
    assert "location" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["transcode", "export"])
async def test_lock_export_rejects_spec_and_channel_overrides(
    lock_client, lock_document, endpoint
):
    response = await lock_client.post(
        f"/{endpoint}?format=workspace-lock&spec=numpy&channel=defaults",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "specs": [],
            "channels": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["reasons"] == [
        "additional specs require solving",
        "channel overrides require solving",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["parse", "export?format=workspace-lock"])
async def test_lock_requests_enforce_target_limit_before_export(
    lock_client, lock_document, monkeypatch, endpoint
):
    monkeypatch.setenv("CONDA_PRESTO_MAX_PLATFORMS", "1")
    response = await lock_client.post(
        f"/{endpoint}",
        json={"file": yaml.safe_dump(lock_document), "filename": "conda.lock"},
    )

    assert response.status_code == 400, response.text
    assert "Too many lockfile targets" in response.text
    assert "location" not in response.headers


@pytest.mark.anyio
async def test_lock_requests_enforce_channel_limit_without_channel_fetches(
    lock_client, lock_document, monkeypatch
):
    monkeypatch.setenv("CONDA_PRESTO_MAX_CHANNELS", "1")
    lock_document["environments"]["default"]["channels"].append(
        {"url": "https://example.invalid/unused"}
    )
    response = await lock_client.post(
        "/export?format=workspace-lock",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
        },
    )

    assert response.status_code == 400, response.text
    assert "Too many lockfile channels" in response.text
    assert "location" not in response.headers


@pytest.mark.anyio
async def test_retention_identifies_full_source_selection_format_and_operation(
    lock_client, lock_document, monkeypatch
):
    keys = []
    remember = ResultCache.remember

    async def record_key(self, key, *args, **kwargs):
        keys.append(key)
        return await remember(self, key, *args, **kwargs)

    monkeypatch.setattr(ResultCache, "remember", record_key)
    body = {
        "file": yaml.safe_dump(lock_document),
        "filename": "conda.lock",
        "environments": ["default"],
        "platforms": ["linux-cuda"],
    }
    response = await lock_client.post("/export?format=workspace-lock", json=body)
    alias = await lock_client.post("/export?format=conda-workspaces-lock-v1", json=body)
    operation = await lock_client.post("/transcode?format=workspace-lock", json=body)
    lock_document["packages"][1]["license"] = "MIT"
    body["file"] = yaml.safe_dump(lock_document)
    changed_source = await lock_client.post("/export?format=workspace-lock", json=body)

    for candidate in (response, alias, operation, changed_source):
        assert candidate.status_code == 200, candidate.text
        assert candidate.content == response.content
    assert keys[0] == keys[1]
    assert len({keys[0], keys[2], keys[3]}) == 3


@pytest.mark.anyio
async def test_capabilities_report_named_lock_operations(lock_client):
    response = await lock_client.get("/capabilities")

    assert response.json()["workspace_lock_parse"] is True
    assert response.json()["workspace_lock_export"] is True
