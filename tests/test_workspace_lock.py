"""Workspace lock selection delegates to Workspaces without solving."""

from __future__ import annotations

import hashlib
import json
import tomllib

import msgspec
import pytest
from conda.common.serialize.yaml import dumps as yaml_dumps
from conda.core.package_cache_data import PackageCacheData, ProgressiveFetchExtract
from conda.exceptions import CondaError
from conda_workspaces.lockfile import CondaLockLoader, load_lockfile_data
from conda_workspaces.resolver import ResolvedEnvironment

from conda_presto import workspace_lock
from conda_presto.workspace_lock import WorkspaceLockInput


@pytest.fixture
def offline_lock_operations(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("lock operations must not solve or fetch packages")

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fail)
    monkeypatch.setattr(ProgressiveFetchExtract, "execute", fail)
    monkeypatch.setattr(PackageCacheData, "query_all", fail)


def test_lock_discovery_preserves_names_without_selecting_host(
    workspace_lock_path, offline_lock_operations
):
    parsed = WorkspaceLockInput.from_path(workspace_lock_path)
    assert msgspec.to_builtins(parsed.result) == {
        "format": "conda-workspaces-lock-v1",
        "environments": [
            {
                "name": name,
                "platforms": {
                    "cpu": "linux-64",
                    "gpu": "linux-64",
                    "osx-arm64": "osx-arm64",
                },
            }
            for name in ("default", "test")
        ],
        "selected": [],
    }
    with pytest.raises(ValueError, match="Select at least one"):
        parsed.render("workspace-lock")


@pytest.mark.parametrize("format_name", ["workspace-lock", "conda-workspaces-lock-v1"])
def test_lock_extraction_preserves_source_entries_and_package_metadata(
    workspace_lock_path, workspace_lock_data, offline_lock_operations, format_name
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu", "cpu"]
    )
    data = load_lockfile_data(parsed.render(format_name))
    assert list(data["environments"]) == ["test"]
    assert list(data["environments"]["test"]["packages"]) == ["cpu"]
    assert data["environments"]["test"]["source_metadata"] == {"environment": "test"}
    assert data["metadata"] == workspace_lock_data["metadata"]
    assert data["packages"] == workspace_lock_data["packages"][:1]
    selected = CondaLockLoader(workspace_lock_path, data=data)
    env = selected.env_for(
        "cpu", "test", package_platform="linux-64", metadata_only=True
    )
    assert env.explicit_packages[0].build_number == 7
    assert env.explicit_packages[0].sha256 == "a" * 64


@pytest.mark.parametrize(
    "environments,platforms,format_name,message",
    [
        (["missing"], None, "workspace-lock", "missing"),
        (["test"], ["missing"], "workspace-lock", "Unknown lockfile target"),
        (["test"], ["linux-64"], "workspace-lock", "ambiguous"),
        ([], None, "workspace-lock", "Select at least one"),
        (["test"], [], "workspace-lock", "Select at least one"),
        (None, None, "explicit", "Select one environment"),
        (["test"], ["cpu", "gpu"], "pixi-toml", "sharing a conda subdir"),
        (["test"], ["cpu", "osx-arm64"], "environment-yaml", "Select one target"),
    ],
)
def test_lock_selection_rejects_missing_ambiguous_or_unrepresentable_output(
    workspace_lock_path, environments, platforms, format_name, message
):
    with pytest.raises(ValueError, match=message):
        parsed = WorkspaceLockInput.from_path(
            workspace_lock_path,
            environments=environments,
            platforms=platforms,
            select_all=True,
        )
        parsed.render(format_name)


@pytest.mark.parametrize(
    "format_name", ["conda-toml", "pixi-toml", "pyproject-toml", "environment-yaml"]
)
def test_locked_normalized_exports_use_registered_exporters(
    workspace_lock_path, offline_lock_operations, format_name
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    )
    rendered = parsed.render(format_name)
    assert "probe" in rendered
    assert "conda-forge" in rendered
    assert "source_metadata" not in rendered


def test_noarch_only_logical_target_can_be_inspected_and_extracted(
    workspace_lock_path, workspace_lock_data, offline_lock_operations
):
    data = workspace_lock_data
    data["environments"] = {"test": data["environments"]["test"]}
    data["environments"]["test"]["packages"] = {
        "cpu": data["environments"]["test"]["packages"]["cpu"]
    }
    data["packages"] = data["packages"][:1]
    workspace_lock_path.write_text(yaml_dumps(data).replace("linux-64", "noarch"))
    parsed = WorkspaceLockInput.from_path(workspace_lock_path, select_all=True)
    assert parsed.result.environments[0].platforms == {"cpu": None}
    assert parsed.result.selected[0].subdir is None
    assert "noarch" in parsed.render("workspace-lock")
    with pytest.raises(ValueError, match="Cannot infer"):
        parsed.render("explicit")


def test_lock_input_rejects_credentials_before_upstream_redaction(workspace_lock_path):
    workspace_lock_path.write_text(
        workspace_lock_path.read_text().replace(
            "https://conda.anaconda.org/", "https://user:secret@conda.anaconda.org/"
        )
    )
    with pytest.raises(ValueError, match="cannot contain URL credentials"):
        WorkspaceLockInput.from_path(workspace_lock_path)


@pytest.mark.parametrize("limit", ["MAX_PLATFORMS", "MAX_CHANNELS"])
def test_lock_input_enforces_request_limits_before_render(
    monkeypatch, workspace_lock_path, limit
):
    monkeypatch.setattr(workspace_lock, limit, 0)
    with pytest.raises(ValueError, match="Too many lockfile"):
        WorkspaceLockInput.from_path(workspace_lock_path, select_all=True)


def test_lock_cache_identity_includes_source_selections_and_providers(
    workspace_lock_path,
):
    cpu = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    )
    gpu = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["gpu"]
    )
    identity = cpu.cache_identity()
    assert (
        identity["source"]
        == hashlib.sha256(workspace_lock_path.read_bytes()).hexdigest()
    )
    assert identity["selected"] != gpu.cache_identity()["selected"]
    assert all(identity["providers"].values())
    workspace_lock_path.write_text(
        workspace_lock_path.read_text() + "\n# source change\n"
    )
    assert (
        identity
        != WorkspaceLockInput.from_path(
            workspace_lock_path, environments=["test"], platforms=["cpu"]
        ).cache_identity()
    )


@pytest.mark.parametrize("format_name", ["conda-lock-v1", "rattler-lock-v6"])
def test_lock_conversion_rejects_exporters_that_drop_source_metadata(
    workspace_lock_path, workspace_lock_data, offline_lock_operations, format_name
):
    workspace_lock_data["packages"][0]["constrains"] = ["optional >=2"]
    workspace_lock_path.write_text(yaml_dumps(workspace_lock_data))
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    )
    with pytest.raises(ValueError, match="conda-lockfiles"):
        parsed.render(format_name)


@pytest.fixture
def companion_manifest():
    return (
        '[workspace]\nchannels = ["conda-forge"]\n'
        'platforms = [{name = "cpu", platform = "linux-64"}]\n'
        '[dependencies]\nprobe = ">=1"\n[environments]\ntest = []\n'
    )


def test_each_sbom_preserves_named_targets_and_exact_components(
    workspace_lock_path, workspace_lock_data, offline_lock_operations
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test", "default"], platforms=["cpu", "gpu"]
    )
    documents = parsed.render_each("cyclonedx")
    assert [(doc.environment, doc.platform, doc.subdir) for doc in documents] == [
        (name, target, "linux-64")
        for name in ("test", "default")
        for target in ("cpu", "gpu")
    ]
    for document in documents:
        sbom = json.loads(document.content)
        root = sbom["metadata"]["component"]
        assert root["name"] == document.environment
        properties = {item["name"]: item["value"] for item in root["properties"]}
        assert properties["conda:environment:root-dependency-source"] == (
            "inferred-graph-roots"
        )
        component = sbom["components"][0]
        assert component["version"] == "1.0"
        assert {item["alg"]: item["content"] for item in component["hashes"]} == {
            "SHA-256": "a" * 64,
            "MD5": "b" * 32,
        }
        source = workspace_lock_data["environments"][document.environment]["packages"][
            document.platform
        ][0]["conda"]
        assert source in [item["url"] for item in component["externalReferences"]]


def test_companion_roots_use_workspaces_matching_and_affect_cache_identity(
    workspace_lock_path, companion_manifest, offline_lock_operations
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    )
    enriched = parsed.with_manifest(companion_manifest, "conda.toml")
    document = json.loads(enriched.render("cyclonedx"))
    properties = {
        item["name"]: item["value"]
        for item in document["metadata"]["component"]["properties"]
    }
    assert (
        properties["conda:environment:root-dependency-source"] == "requested-packages"
    )
    assert not parsed.environment(parsed.result.selected[0]).requested_packages
    assert enriched.cache_identity() != parsed.cache_identity()
    assert enriched.cache_identity()["manifest"] == {
        "source": hashlib.sha256(companion_manifest.encode()).hexdigest(),
        "format": "conda-toml",
    }


@pytest.mark.parametrize("format_name", ["conda-toml", "pixi-toml", "pyproject-toml"])
@pytest.mark.parametrize("subdir", ["linux-64", "noarch"])
@pytest.mark.parametrize("export_each", [False, True], ids=["combined", "per-target"])
def test_companion_manifest_keeps_exact_records_in_normalized_exports(
    workspace_lock_path,
    workspace_lock_data,
    companion_manifest,
    offline_lock_operations,
    format_name,
    subdir,
    export_each,
):
    data = workspace_lock_data
    data["packages"][0]["depends"] = ["gpu-probe >=1"]
    data["environments"]["test"]["packages"]["cpu"].append(
        {"conda": data["packages"][1]["conda"]}
    )
    workspace_lock_path.write_text(
        yaml_dumps(data).replace("/linux-64/", f"/{subdir}/")
    )
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    ).with_manifest(companion_manifest, "conda.toml")
    content = (
        parsed.render_each(format_name)[0].content
        if export_each
        else parsed.render(format_name)
    )
    manifest = tomllib.loads(content)
    if format_name == "pyproject-toml":
        manifest = manifest["tool"]["conda"]
    assert manifest["workspace"]["platforms"] == ["linux-64"]
    dependencies = manifest["dependencies"]
    assert set(dependencies) == {"probe", "gpu-probe"}
    for name, dependency in dependencies.items():
        assert dependency["url"] == (
            f"https://conda.anaconda.org/conda-forge/{subdir}/{name}-1.0-h123_0.conda"
        )
        assert dependency["version"] == "1.0"
        assert dependency["build"] == "h123_0"
        assert dependency["sha256"] == "a" * 64
        assert dependency["md5"] == "b" * 32


@pytest.mark.parametrize("format_name", ["cyclonedx", "conda-toml"])
@pytest.mark.parametrize(
    "before,after,message",
    [
        ('probe = ">=1"', 'probe = ">=2"', "do not satisfy"),
        ('probe = ">=1"', 'missing = "*"', "do not satisfy"),
        ('"conda-forge"', '"other-channel"', "channels do not match"),
        ('platform = "linux-64"', 'platform = "osx-arm64"', "platform does not match"),
        ("test = []", "other = []", "Unknown workspace environment"),
        ('name = "cpu"', 'name = "other"', "Unknown workspace platform"),
        (
            '[dependencies]\nprobe = ">=1"',
            '[dependencies]\nprobe = ">=1"\n[pypi-dependencies]\nrequests = "*"',
            "PyPI dependencies",
        ),
    ],
)
def test_companion_manifest_mismatches_reject_export(
    workspace_lock_path,
    companion_manifest,
    offline_lock_operations,
    before,
    after,
    message,
    format_name,
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    )
    with pytest.raises((CondaError, ValueError), match=message):
        parsed.with_manifest(
            companion_manifest.replace(before, after), "conda.toml"
        ).render(format_name)


def test_each_export_returns_no_documents_when_a_later_target_fails(
    workspace_lock_path, companion_manifest, offline_lock_operations
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu", "gpu"]
    )
    manifest = companion_manifest.replace(
        'platform = "linux-64"}]',
        'platform = "linux-64"}, {name = "gpu", platform = "linux-64"}]',
    )
    with pytest.raises((CondaError, ValueError), match="do not satisfy"):
        parsed.with_manifest(manifest, "conda.toml").render_each("cyclonedx")


def test_companion_context_is_not_silently_ignored_by_source_extraction(
    workspace_lock_path, companion_manifest
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    ).with_manifest(companion_manifest, "conda.toml")
    with pytest.raises(ValueError, match="cannot modify source lockfile extraction"):
        parsed.render("workspace-lock")


def test_companion_manifest_supplies_noarch_only_logical_target_subdir(
    workspace_lock_path, companion_manifest, offline_lock_operations
):
    workspace_lock_path.write_text(
        workspace_lock_path.read_text().replace("/linux-64/", "/noarch/")
    )
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    ).with_manifest(companion_manifest, "conda.toml")
    document = parsed.render_each("cyclonedx")[0]
    assert document.subdir == "linux-64"
    assert "noarch" in json.loads(document.content)["components"][0]["purl"]
