"""HTTP discovery and exact export of named locked environments."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml
from conda.models.match_spec import MatchSpec
from httpx import ASGITransport, AsyncClient
from litestar import Litestar, Router

import conda_presto.app as app_module
from conda_presto.app import (
    capabilities,
    export_post,
    parse,
    resolve_post,
    result_get,
    sbom_post,
    transcode_post,
)
from conda_presto.cache import ResultCache


@pytest.fixture()
async def lock_client(monkeypatch, request):
    def unexpected_work(*args, **kwargs):
        pytest.fail("Lock export must not solve, capture repodata or render in HTTP")

    monkeypatch.setattr(app_module, "solve", unexpected_work)
    monkeypatch.setattr(app_module, "solve_environments", unexpected_work)
    monkeypatch.setattr(app_module.RepodataSnapshot, "capture", unexpected_work)
    monkeypatch.setattr(app_module.OutputFormat, "render", unexpected_work)
    prefix = getattr(request, "param", "/")
    app = Litestar(
        route_handlers=[
            Router(
                path=prefix,
                route_handlers=[
                    parse,
                    export_post,
                    transcode_post,
                    resolve_post,
                    result_get,
                    sbom_post,
                    capabilities,
                ],
            )
        ]
    )
    app.state.solver_limiter = None
    app.state.result_cache = ResultCache(max_size=256)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"http://test{prefix}"
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


@pytest.fixture()
def sbom_lock_document(lock_document):
    libraries = []
    for package, digest in zip(lock_document["packages"], ("c", "d"), strict=True):
        package["depends"] = ["library >=1.0"]
        libraries.append(
            package
            | {
                "name": "library",
                "conda": package["conda"].replace("probe-", "library-"),
                "depends": [],
                "sha256": digest * 64,
                "md5": digest * 32,
            }
        )
    lock_document["packages"].extend(libraries)
    for environment in lock_document["environments"].values():
        environment["packages"] = {
            target: [
                {"conda": package["conda"]}
                for package in lock_document["packages"]
                if package["subdir"] == subdir
            ]
            for target, subdir in (
                ("linux-cuda", "linux-64"),
                ("osx-arm64", "osx-arm64"),
            )
        }
    return lock_document


@pytest.fixture()
def sbom_manifest(sbom_lock_document):
    channel = sbom_lock_document["environments"]["default"]["channels"][0]["url"]
    return (
        '[workspace]\nname = "saved"\n'
        f"channels = [{json.dumps(channel)}]\n"
        'platforms = [{name = "linux-cuda", platform = "linux-64"}, "osx-arm64"]\n'
        '[dependencies]\nlibrary = ">=1.0"\n'
        "[environments]\ndefault = []\ntest = []\n"
    )


@pytest.mark.anyio
async def test_locked_sbom_collection_preserves_records_edges_and_retained_bytes(
    lock_client, sbom_lock_document
):
    response = await lock_client.post(
        "/sbom",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            "environments": ["default", "test"],
            "platforms": ["linux-cuda", "osx-arm64"],
        },
    )

    assert response.status_code == 200, response.text
    items = response.json()["sboms"]
    assert {
        (item["environment"], item["platform"], item["subdir"]) for item in items
    } == {
        (environment, platform, subdir)
        for environment in ("default", "test")
        for platform, subdir in (("linux-cuda", "linux-64"), ("osx-arm64", "osx-arm64"))
    }
    assert len(items) == 4
    for item in items:
        body = item["content"].encode("utf-8")
        document = json.loads(body)
        assert document["bomFormat"] == "CycloneDX"
        assert document["specVersion"] == "1.7"
        root = document["metadata"]["component"]
        assert root["name"] == item["environment"]
        properties = {entry["name"]: entry["value"] for entry in root["properties"]}
        assert (
            properties["conda:environment:root-dependency-source"]
            == "inferred-graph-roots"
        )
        assert properties["conda:environment:scope"] == "resolved-conda-packages"
        assert properties["conda:environment:platform"] == item["subdir"]
        components = {entry["name"]: entry for entry in document["components"]}
        assert set(components) == {"probe", "library"}
        edges = {entry["ref"]: entry["dependsOn"] for entry in document["dependencies"]}
        assert edges[root["bom-ref"]] == [components["probe"]["bom-ref"]]
        assert edges[components["probe"]["bom-ref"]] == [
            components["library"]["bom-ref"]
        ]
        for package in sbom_lock_document["packages"]:
            if package["subdir"] != item["subdir"]:
                continue
            component = components[package["name"]]
            assert component["version"] == package["version"]
            assert {
                entry["alg"]: entry["content"] for entry in component["hashes"]
            } == {
                "MD5": package["md5"],
                "SHA-256": package["sha256"],
            }
            assert component["externalReferences"] == [
                {"type": "distribution", "url": package["conda"]}
            ]
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
        retained = await lock_client.get(item["location"])
        assert retained.status_code == 200
        assert retained.content == body
        assert retained.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_locked_sbom_collection_keeps_logical_targets_with_one_subdir(
    lock_client, sbom_lock_document
):
    targets = sbom_lock_document["environments"]["default"]["packages"]
    targets["linux-debug"] = [entry.copy() for entry in targets["linux-cuda"]]
    response = await lock_client.post(
        "/sbom",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-cuda", "linux-debug"],
        },
    )

    assert response.status_code == 200, response.text
    assert [
        (item["platform"], item["subdir"]) for item in response.json()["sboms"]
    ] == [
        ("linux-cuda", "linux-64"),
        ("linux-debug", "linux-64"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["sbom", "export?format=cyclonedx-json-v1.7"])
async def test_manifest_context_changes_sbom_roots_after_matching_saved_records(
    lock_client, sbom_lock_document, sbom_manifest, endpoint
):
    request = {
        "file": yaml.safe_dump(sbom_lock_document),
        "filename": "conda.lock",
        "environments": ["default"],
        "platforms": ["linux-cuda"],
    }
    inferred = await lock_client.post(f"/{endpoint}", json=request)
    declared = await lock_client.post(
        f"/{endpoint}",
        json=request | {"manifest": sbom_manifest, "manifest_filename": "conda.toml"},
    )

    for response, source, expected_root in (
        (inferred, "inferred-graph-roots", "probe"),
        (declared, "requested-packages", "library"),
    ):
        assert response.status_code == 200, response.text
        if endpoint == "sbom":
            item = response.json()["sboms"][0]
            document = json.loads(item["content"])
            location = item["location"]
            body = item["content"].encode("utf-8")
        else:
            document = response.json()
            location = response.headers["location"]
            body = response.content
        root = document["metadata"]["component"]
        properties = {entry["name"]: entry["value"] for entry in root["properties"]}
        assert properties["conda:environment:root-dependency-source"] == source
        components = {entry["name"]: entry for entry in document["components"]}
        edges = {entry["ref"]: entry["dependsOn"] for entry in document["dependencies"]}
        assert edges[root["bom-ref"]] == [components[expected_root]["bom-ref"]]
        retained = await lock_client.get(location)
        assert retained.content == body


@pytest.mark.anyio
@pytest.mark.parametrize(
    "selection",
    [
        pytest.param({}, id="missing-selectors"),
        pytest.param({"platforms": ["linux-cuda"]}, id="missing-environment"),
        pytest.param({"environments": ["default"]}, id="missing-platform"),
        pytest.param(
            {"environments": [], "platforms": ["linux-cuda"]}, id="empty-environments"
        ),
        pytest.param(
            {"environments": ["default"], "platforms": []}, id="empty-platforms"
        ),
        pytest.param(
            {"environments": ["absent"], "platforms": ["linux-cuda"]},
            id="unknown-environment",
        ),
        pytest.param(
            {"environments": ["default"], "platforms": ["absent"]}, id="unknown-target"
        ),
    ],
)
async def test_locked_sbom_requires_explicit_valid_selection(
    lock_client, sbom_lock_document, selection
):
    response = await lock_client.post(
        "/sbom",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            **selection,
        },
    )

    assert response.status_code == 400, response.text
    assert "error" in response.json()
    assert "sboms" not in response.json()


@pytest.mark.anyio
@pytest.mark.parametrize("override", [{"specs": ["numpy"]}, {"channels": ["defaults"]}])
async def test_locked_sbom_rejects_spec_and_channel_overrides(
    lock_client, sbom_lock_document, override
):
    response = await lock_client.post(
        "/sbom",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-cuda"],
            **override,
        },
    )

    assert response.status_code == 400, response.text
    assert "error" in response.json()
    assert "sboms" not in response.json()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "problem", ["bad-hash", "external-reference", "missing-platform-metadata"]
)
async def test_locked_sbom_rejects_invalid_later_target_without_partial_collection(
    lock_client, sbom_lock_document, monkeypatch, problem
):
    stored = []
    remember = ResultCache.remember

    async def record_store(self, *args, **kwargs):
        stored.append(args)
        return await remember(self, *args, **kwargs)

    monkeypatch.setattr(ResultCache, "remember", record_store)
    if problem == "bad-hash":
        sbom_lock_document["packages"][1]["sha256"] = "invalid"
    elif problem == "external-reference":
        sbom_lock_document["environments"]["default"]["packages"]["osx-arm64"].append(
            {"pypi": "https://example.invalid/example.whl"}
        )
    else:
        targets = sbom_lock_document["environments"]["default"]["packages"]
        targets["unknown"] = []
        del targets["osx-arm64"]
    response = await lock_client.post(
        "/sbom",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": [
                "linux-cuda",
                "unknown" if problem == "missing-platform-metadata" else "osx-arm64",
            ],
        },
    )

    assert response.status_code == 400, response.text
    assert "error" in response.json()
    assert "sboms" not in response.json()
    assert stored == []


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["sbom", "export?format=cyclonedx-json-v1.7"])
@pytest.mark.parametrize("mismatch", ["requirement", "channel", "platform", "filename"])
async def test_locked_sbom_rejects_mismatched_companion_manifest(
    lock_client, sbom_lock_document, sbom_manifest, endpoint, mismatch
):
    if mismatch == "requirement":
        manifest = sbom_manifest.replace('library = ">=1.0"', 'library = ">=2.0"')
    elif mismatch == "channel":
        channel = sbom_lock_document["environments"]["default"]["channels"][0]["url"]
        manifest = sbom_manifest.replace(channel, "https://example.invalid/other")
    elif mismatch == "platform":
        manifest = sbom_manifest.replace('platform = "linux-64"', 'platform = "win-64"')
    else:
        manifest = sbom_manifest
    response = await lock_client.post(
        f"/{endpoint}",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            "environments": ["default"],
            "platforms": ["linux-cuda"],
            "manifest": manifest,
            "manifest_filename": "requirements.txt"
            if mismatch == "filename"
            else "conda.toml",
        },
    )

    assert response.status_code == 400, response.text
    assert "error" in response.json()
    assert "sboms" not in response.json()
    assert "location" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize("context_fields", ["manifest", "manifest_filename", "both"])
async def test_transcode_rejects_companion_manifest_context(
    lock_client, sbom_lock_document, sbom_manifest, context_fields
):
    context = {"manifest": sbom_manifest, "manifest_filename": "conda.toml"}
    if context_fields != "both":
        context = {context_fields: context[context_fields]}
    response = await lock_client.post(
        "/transcode?format=workspace-lock",
        json={
            "file": yaml.safe_dump(sbom_lock_document),
            "filename": "conda.lock",
            **context,
        },
    )

    assert response.status_code == 400, response.text
    assert "manifest" in response.text.lower()


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
    assert response.json()["workspace_lock_sbom"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("lock_client", ["/", "/api/"], indirect=True)
@pytest.mark.parametrize("endpoint,status", [("export", 200), ("transcode", 400)])
async def test_export_format_support_is_independent_of_route_prefix(
    lock_client, lock_document, endpoint, status
):
    response = await lock_client.post(
        f"{endpoint}?format=environment-yaml",
        json={
            "file": yaml.safe_dump(lock_document),
            "filename": "conda.lock",
            "environments": ["test"],
            "platforms": ["linux-64"],
        },
    )

    assert response.status_code == status, response.text
    if endpoint == "export":
        assert yaml.safe_load(response.text)["dependencies"] == ["probe=1.0=h123_0"]
    else:
        assert response.json()["reasons"] == ["output format is not a lockfile"]


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint,status", [("export", 200), ("transcode", 400)])
@pytest.mark.parametrize(
    "filename,content,selectors",
    [
        (
            "environment.yml",
            "name: declared\nchannels: [conda-forge]\ndependencies: [python>=3.12]\n",
            {},
        ),
        (
            "requirements.txt",
            "python>=3.12\n",
            {},
        ),
        (
            "conda.toml",
            (
                '[workspace]\nname = "declared"\nchannels = ["conda-forge"]\n'
                'platforms = ["linux-64"]\n[dependencies]\npython = ">=3.12"\n'
            ),
            {"environments": ["default"], "platforms": ["linux-64"]},
        ),
    ],
    ids=["environment-yaml", "requirements", "workspace"],
)
async def test_export_accepts_declarations_without_solving(
    lock_client, endpoint, status, filename, content, selectors
):
    response = await lock_client.post(
        f"/{endpoint}?format=environment-yaml",
        json={"file": content, "filename": filename, **selectors},
    )

    assert response.status_code == status, response.text
    if endpoint == "export":
        assert [
            MatchSpec(spec) for spec in yaml.safe_load(response.text)["dependencies"]
        ] == [MatchSpec("python>=3.12")]
        assert response.headers["cache-control"] == "no-store"
    else:
        assert "input file is not a lockfile" in response.json()["reasons"]
