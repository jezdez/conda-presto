"""Workspace lock selection delegates to Workspaces without solving."""

from __future__ import annotations

import hashlib
import json
import os

import msgspec
import pytest
from conda.base.context import context
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


@pytest.fixture
def workspace_lock_check(
    workspace_consistent_lock_path, workspace_consistent_manifest_text
):
    return WorkspaceLockInput.from_path(workspace_consistent_lock_path).with_manifest(
        workspace_consistent_manifest_text, "conda.toml"
    )


def test_consistency_checks_every_named_target_without_fetching(
    workspace_lock_check, offline_lock_operations
):
    before = workspace_lock_check.loader.path.read_bytes()
    result = workspace_lock_check.check_consistency()
    assert result.consistent
    assert [(target.environment, target.platform) for target in result.targets] == [
        (name, target)
        for name in ("default", "test")
        for target in ("cpu", "gpu", "osx-arm64")
    ]
    assert all(target.consistent and target.reason is None for target in result.targets)
    assert workspace_lock_check.loader.path.read_bytes() == before


@pytest.mark.parametrize(
    "field,requirement,consistent,reason",
    [
        ("depends", "missing-dependency >=1", False, "missing-dependency"),
        ("constrains", "probe >=2", False, "constrains"),
        ("constrains", "absent >=2", True, None),
        ("depends", "__glibc >=2.29", False, "__glibc"),
        ("depends", "__glibc >=2.28", True, None),
    ],
)
def test_consistency_keeps_provider_dependency_constraint_and_virtual_results(
    workspace_lock_check,
    offline_lock_operations,
    field,
    requirement,
    consistent,
    reason,
):
    workspace_lock_check.source_data["packages"][0][field] = [requirement]
    result = workspace_lock_check.check_consistency()
    assert result.consistent is consistent
    for target in result.targets:
        assert target.consistent is (consistent or target.platform != "cpu")
        if not target.consistent:
            assert reason in target.reason


@pytest.mark.parametrize(
    "missing", ["all-environments", "environment", "platform", "all-platforms"]
)
def test_missing_lock_declarations_are_mismatches(
    workspace_consistent_lock_path,
    workspace_consistent_lock_data,
    workspace_consistent_manifest_text,
    offline_lock_operations,
    missing,
):
    data = workspace_consistent_lock_data
    if missing == "all-environments":
        data["environments"] = {}
    elif missing == "environment":
        del data["environments"]["test"]
    elif missing == "platform":
        del data["environments"]["test"]["packages"]["gpu"]
    else:
        data["environments"]["test"]["packages"] = {}
    workspace_consistent_lock_path.write_text(yaml_dumps(data))
    parsed = WorkspaceLockInput.from_path(
        workspace_consistent_lock_path, allow_empty=True
    )
    result = parsed.with_manifest(
        workspace_consistent_manifest_text, "conda.toml"
    ).check_consistency()
    assert not result.consistent
    assert len(result.targets) == 6
    assert all("missing" in target.reason for target in result.targets)


@pytest.mark.parametrize(
    "change,reason",
    [
        ('probe = "==1.0"', "probe"),
        ('channels = ["conda-forge"]', "Channel mismatch"),
        ("test = []", "not declared"),
    ],
)
def test_changed_manifest_returns_provider_mismatch(
    workspace_consistent_lock_path,
    workspace_consistent_manifest_text,
    offline_lock_operations,
    change,
    reason,
):
    replacement = {
        'probe = "==1.0"': 'probe = ">=2"',
        'channels = ["conda-forge"]': 'channels = ["other", "conda-forge"]',
        "test = []": "",
    }[change]
    manifest = workspace_consistent_manifest_text.replace(change, replacement)
    result = (
        WorkspaceLockInput.from_path(workspace_consistent_lock_path)
        .with_manifest(manifest, "conda.toml")
        .check_consistency()
    )
    assert not result.consistent
    assert any(reason in target.reason for target in result.targets if target.reason)


@pytest.mark.parametrize("requirement", ["__cuda >=12", "__archspec 1 x86_64_v3"])
def test_check_disables_host_gpu_and_cpu_detection(
    monkeypatch, workspace_lock_check, offline_lock_operations, requirement
):
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "99")
    monkeypatch.setenv("CONDA_OVERRIDE_ARCHSPEC", "x86_64_v3")
    workspace_lock_check.source_data["packages"][0]["depends"] = [requirement]
    result = workspace_lock_check.check_consistency()
    assert not result.consistent
    assert all(
        not target.consistent for target in result.targets if target.platform == "cpu"
    )
    assert os.environ["CONDA_OVERRIDE_CUDA"] == "99"
    assert os.environ["CONDA_OVERRIDE_ARCHSPEC"] == "x86_64_v3"


def test_check_uses_each_environments_system_requirements(
    monkeypatch,
    workspace_consistent_lock_path,
    workspace_consistent_lock_data,
    offline_lock_operations,
):
    data = workspace_consistent_lock_data
    for env in data["environments"].values():
        env["packages"] = {"linux-64": env["packages"]["cpu"]}
    data["packages"][0]["depends"] = ["__glibc >=2.35"]
    workspace_consistent_lock_path.write_text(yaml_dumps(data))
    manifest = """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]
[dependencies]
probe = "==1.0"
[system-requirements]
libc = "2.28"
[feature.new.system-requirements]
libc = "2.40"
[environments]
test = ["new"]
"""
    monkeypatch.setenv("CONDA_OVERRIDE_GLIBC", "99")
    result = (
        WorkspaceLockInput.from_path(workspace_consistent_lock_path)
        .with_manifest(manifest, "conda.toml")
        .check_consistency()
    )
    assert [(target.environment, target.consistent) for target in result.targets] == [
        ("default", False),
        ("test", True),
    ]
    assert os.environ["CONDA_OVERRIDE_GLIBC"] == "99"


def test_check_restores_virtual_context_after_provider_failure(
    monkeypatch, workspace_lock_check
):
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "99")
    before = dict(os.environ)
    original_subdir = context.subdir

    def fail(*args, **kwargs):
        raise RuntimeError("provider failed")

    monkeypatch.setattr(workspace_lock, "check_lockfile_satisfiability", fail)
    with pytest.raises(RuntimeError, match="provider failed"):
        workspace_lock_check.check_consistency()
    assert dict(os.environ) == before
    assert context.subdir == original_subdir


@pytest.mark.parametrize(
    "declaration,reason",
    [
        ('[target.gpu.pypi-dependencies]\nrequests = "*"\n', "PyPI"),
        ('[system-requirements]\narchspec = "x86_64_v3"\n', "archspec"),
    ],
)
def test_check_rejects_requirements_the_provider_cannot_check(
    workspace_consistent_lock_path,
    workspace_consistent_manifest_text,
    declaration,
    reason,
):
    parsed = WorkspaceLockInput.from_path(workspace_consistent_lock_path).with_manifest(
        workspace_consistent_manifest_text + declaration, "conda.toml"
    )
    with pytest.raises(ValueError, match=reason):
        parsed.check_consistency()


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
):
    parsed = WorkspaceLockInput.from_path(
        workspace_lock_path, environments=["test"], platforms=["cpu"]
    )
    with pytest.raises((CondaError, ValueError), match=message):
        parsed.with_manifest(
            companion_manifest.replace(before, after), "conda.toml"
        ).render("cyclonedx")


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
