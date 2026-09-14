"""Workspace lock selection delegates to Workspaces without solving."""

from __future__ import annotations

import hashlib

import msgspec
import pytest
from conda.common.serialize.yaml import dumps as yaml_dumps
from conda.core.package_cache_data import PackageCacheData, ProgressiveFetchExtract
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
