"""Workspace manifest discovery and selection behavior."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import msgspec
import pytest
from conda.base.context import context
from conda.exceptions import PackagesNotFoundError
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord
from conda_workspaces.lockfile import load_lockfile_data
from conda_workspaces.resolver import ResolvedEnvironment

from conda_presto import workspace
from conda_presto.exceptions import WorkspaceSolveError
from conda_presto.workspace import WorkspaceInput


@pytest.fixture
def manifest(tmp_path):
    def write(content, filename="conda.toml"):
        path = tmp_path / filename
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    return write


@pytest.fixture
def rich_manifest(manifest):
    return manifest("""
        [workspace]
        channels = ["conda-forge"]
        channel-priority = "strict"
        platforms = [
            { name = "cpu", platform = "linux-64", libc = "2.28" },
            { name = "gpu", platform = "linux-64", cuda = "12" },
            "osx-arm64",
        ]
        [dependencies]
        python = ">=3.11"
        [feature.test.dependencies]
        pytest = "*"
        [environments]
        default = []
        [environments.test]
        features = ["test"]
        [environments.test.target.gpu.dependencies]
        cupy = ">=13"
    """)


@pytest.fixture
def workspace_solver(monkeypatch):
    calls = []
    failing = set()

    def solve(self, platform, *, prefix, update_names=None):
        prefix = Path(prefix)
        assert update_names is None
        assert prefix.parent.is_dir()
        assert not prefix.exists()
        target = "gpu" if "cuda" in self.system_requirements else "cpu"
        calls.append(
            {
                "target": (self.name, target),
                "prefix": prefix,
                "subdir": context.subdir,
                "solver": context.solver,
                "json": context.json,
                "overrides": dict(context.override_virtual_packages),
            }
        )
        if (self.name, target) in failing:
            raise PackagesNotFoundError(["missing-package"])
        return [
            PackageRecord(
                name=name,
                version="13.1",
                build="0",
                build_number=0,
                channel="conda-forge",
                subdir=platform,
                fn=f"{name}-13.1-0.conda",
                url=f"https://conda.anaconda.org/conda-forge/{platform}/{name}-13.1-0.conda",
                sha256="a" * 64,
                md5="b" * 32,
                depends=["__glibc >=2.17"],
                constrains=["optional >=1"],
                features="sample_feature",
                license="BSD-3-Clause",
                license_family="BSD",
                size=123,
                python_site_packages_path="lib/python3.13/site-packages",
            )
            for name in self.conda_dependencies
            if not name.startswith("__")
        ]

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", solve)
    return calls, failing


@pytest.fixture
def restore_solver_context():
    with (
        context._override("_subdir", "win-64"),
        context._override("_override_virtual_packages", {"win": "10"}),
        context._override("solver", "classic"),
        context._override("json", False),
    ):
        yield
        assert context.subdir == "win-64"
        assert context.override_virtual_packages == {"win": "10"}
        assert context.solver == "classic"
        assert context.json is False


@pytest.mark.parametrize(
    "filename,prefix,format_name",
    [
        ("conda.toml", "", "conda-toml"),
        ("pixi.toml", "", "pixi-toml"),
        ("pyproject.toml", "tool.conda.", "pyproject-toml"),
        ("pyproject.toml", "tool.pixi.", "pyproject-toml"),
    ],
)
def test_workspace_formats_compose_named_requirements(
    manifest, filename, prefix, format_name
):
    path = manifest(
        f"""
        [{prefix}workspace]
        channels = ["conda-forge"]
        platforms = ["linux-64"]
        [{prefix}dependencies]
        python = ">=3.11"
        [{prefix}feature.test.dependencies]
        pytest = "*"
        [{prefix}environments]
        test = ["test"]
    """,
        filename,
    )
    parsed = WorkspaceInput.from_path(path, environments=["test"])
    assert parsed.result.format == format_name
    assert set(parsed.config.environments) == {"default", "test"}
    selected = parsed.result.selected[0]
    assert selected.environment == "test"
    assert selected.platform == selected.subdir == "linux-64"
    assert {MatchSpec(spec).name for spec in selected.specs} == {"python", "pytest"}
    assert selected.channels == ["conda-forge"]


def test_workspace_discovery_retains_config_without_selecting_host(rich_manifest):
    parsed = WorkspaceInput.from_path(rich_manifest)
    public = msgspec.to_builtins(parsed.result)
    assert public["selected"] == []
    assert public["environments"][1] == {
        "name": "test",
        "features": ["test"],
        "no_default_feature": False,
        "platforms": {"cpu": "linux-64", "gpu": "linux-64", "osx-arm64": "osx-arm64"},
    }
    assert parsed.config._manifest_text == rich_manifest.read_text()
    assert parsed.config.platform_system_requirements["gpu"]["cuda"] == "12"
    assert str(rich_manifest.parent) not in msgspec.json.encode(parsed.result).decode()


def test_workspace_selection_keeps_order_and_named_target_settings(rich_manifest):
    parsed = WorkspaceInput.from_path(
        rich_manifest,
        environments=["test", "default", "test"],
        platforms=["gpu", "cpu", "gpu"],
    )
    assert [
        (target.environment, target.platform) for target in parsed.result.selected
    ] == [("test", "gpu"), ("test", "cpu"), ("default", "gpu"), ("default", "cpu")]
    gpu, cpu = parsed.result.selected[:2]
    assert gpu.subdir == cpu.subdir == "linux-64"
    assert gpu.channel_priority == "strict"
    assert gpu.system_requirements["cuda"] == "12"
    assert cpu.system_requirements["glibc"] == "2.28"
    assert "cupy" in {MatchSpec(spec).name for spec in gpu.specs}
    assert "cupy" not in {MatchSpec(spec).name for spec in cpu.specs}


@pytest.mark.parametrize(
    "selectors,error",
    [
        ({"environments": []}, "selectors cannot be empty"),
        ({"platforms": []}, "selectors cannot be empty"),
        ({"environments": ["missing"]}, "Unknown workspace environment"),
        ({"platforms": ["win-64"]}, "Unknown workspace platform"),
        ({"platforms": ["linux-64"]}, "ambiguous"),
    ],
)
def test_workspace_selection_rejects_invalid_or_ambiguous_selectors(
    rich_manifest, selectors, error
):
    with pytest.raises(ValueError, match=error):
        WorkspaceInput.from_path(rich_manifest, **selectors)


def test_workspace_unambiguous_subdir_selects_declared_name(manifest):
    path = manifest(
        '[workspace]\nplatforms = [{ name = "cpu", platform = "linux-64" }]'
    )
    parsed = WorkspaceInput.from_path(path, platforms=["linux-64"])
    assert parsed.result.selected[0].platform == "cpu"


def test_workspace_without_platforms_requires_explicit_target(manifest):
    path = manifest('[workspace]\nname = "example"\n[dependencies]\npython = "*"')
    assert WorkspaceInput.from_path(path).result.environments[0].platforms == {}
    with pytest.raises(ValueError, match="Select an explicit conda platform"):
        WorkspaceInput.from_path(path, environments=["default"])
    with pytest.raises(ValueError, match="Unknown conda platform"):
        WorkspaceInput.from_path(path, platforms=["made-up-platform"])
    assert (
        WorkspaceInput.from_path(path, platforms=["linux-64"])
        .result.selected[0]
        .platform
        == "linux-64"
    )


@pytest.mark.parametrize(
    "source",
    [
        'path = "."',
        'git = "https://example.org/project"',
        'url = "https://example.org/project.whl"',
    ],
)
def test_workspace_discovery_retains_unsupported_pypi_sources(manifest, source):
    path = manifest(f"""
        [workspace]
        platforms = ["linux-64"]
        [feature.local.pypi-dependencies]
        project = {{ {source} }}
        [environments]
        local = ["local"]
    """)
    parsed = WorkspaceInput.from_path(path)
    assert parsed.config.features["local"].pypi_dependencies["project"].to_toml()
    assert WorkspaceInput.from_path(path, environments=["default"]).result.selected
    with pytest.raises(ValueError, match="unsupported PyPI source dependency"):
        WorkspaceInput.from_path(path, environments=["local"])


def test_workspace_selected_pypi_requires_provider_and_preserves_declaration(
    manifest, monkeypatch
):
    path = manifest("""
        [workspace]
        platforms = ["linux-64"]
        [pypi-dependencies]
        requests = { version = ">=2", extras = ["socks"] }
    """)
    monkeypatch.setattr(workspace, "find_spec", lambda name: None)
    assert WorkspaceInput.from_path(path).result.selected == []
    with pytest.raises(ValueError, match="requires conda-pypi"):
        WorkspaceInput.from_path(path, environments=["default"])
    monkeypatch.setattr(workspace, "find_spec", lambda name: object())
    selected = WorkspaceInput.from_path(path, environments=["default"]).result.selected[
        0
    ]
    assert selected.pypi_dependencies == {
        "requests": {"version": ">=2", "extras": ["socks"]}
    }


@pytest.mark.parametrize(
    "declaration,error",
    [
        ("[pypi-dependencies]\nrequests = 7", "PyPI dependency"),
        ('[feature.test.pypi-dependencies]\nrequests = [">=2"]', "PyPI dependency"),
        ("[environments.test]\npypi-dependencies = []", "PyPI dependencies"),
        (
            "[environments.test.target.linux-64.pypi-dependencies]\nrequests = true",
            "PyPI dependency",
        ),
        ("[workspace]\nchannels = [42]", "Channel"),
        ('[feature.test]\nchannels = "conda-forge"', "Channel"),
        ("[environments.test]\nchannels = [{}]", "channels"),
        ("[target.linux-64]\nchannels = [{ channel = 42 }]", "channels"),
    ],
    ids=[
        "pypi-number",
        "feature-pypi-list",
        "environment-pypi-table",
        "target-pypi-bool",
        "channel-number",
        "feature-channels-string",
        "environment-channel-missing-name",
        "target-channel-number",
    ],
)
def test_workspace_rejects_malformed_requirement_tables(manifest, declaration, error):
    if not declaration.startswith("[workspace]"):
        declaration = '[workspace]\nplatforms = ["linux-64"]\n' + declaration
    path = manifest(declaration)
    with pytest.raises(ValueError, match=error):
        WorkspaceInput.from_path(path, platforms=["linux-64"])


def test_workspace_accepts_channel_tables(manifest):
    path = manifest('[workspace]\nchannels = [{ channel = "conda-forge" }]')
    parsed = WorkspaceInput.from_path(path, platforms=["linux-64"])
    assert parsed.result.selected[0].channels == ["conda-forge"]


@pytest.mark.parametrize(
    "source", ['path = "."', 'git = "https://example.org/project"']
)
def test_workspace_rejects_unsupported_conda_source_fields(manifest, source):
    path = manifest(
        '[workspace]\nplatforms = ["linux-64"]\n[dependencies]\n'
        f"project = {{ {source} }}"
    )
    with pytest.raises(ValueError, match="source dependency field"):
        WorkspaceInput.from_path(path)


def test_workspace_selected_conda_url_is_rejected(manifest):
    path = manifest("""
        [workspace]
        platforms = ["linux-64"]
        [dependencies]
        project = { url = "https://example.org/linux-64/project-1-0.conda" }
    """)
    assert WorkspaceInput.from_path(path).config.features["default"].conda_dependencies
    with pytest.raises(ValueError, match="unsupported conda URL dependency"):
        WorkspaceInput.from_path(path, environments=["default"])


def test_workspace_selection_does_not_run_tasks_or_modify_host(manifest, monkeypatch):
    path = manifest("""
        [workspace]
        platforms = ["linux-64"]
        [activation]
        scripts = ["activate.sh"]
        [activation.env]
        EXAMPLE = "changed"
        [tasks.example]
        cmd = "exit 1"
        path = "task-only"
    """)

    def forbidden(*args, **kwargs):
        raise AssertionError("Workspace parsing must not solve")

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", forbidden)
    before = dict(os.environ), context.subdir
    parsed = WorkspaceInput.from_path(path, environments=["default"])
    assert parsed.result.selected
    assert parsed.config.features["default"].activation_scripts == ["activate.sh"]
    assert (dict(os.environ), context.subdir) == before


def test_workspace_matrix_limit_precedes_target_composition(rich_manifest, monkeypatch):
    calls = []
    resolve = workspace.resolve_environment

    def record(config, name, platform=None):
        if platform is not None:
            calls.append((name, platform))
        return resolve(config, name, platform)

    monkeypatch.setattr(workspace, "resolve_environment", record)
    monkeypatch.setattr(workspace, "MAX_PLATFORMS", 1)
    with pytest.raises(ValueError, match="Too many workspace targets"):
        WorkspaceInput.from_path(
            rich_manifest, environments=["test"], platforms=["cpu", "gpu"]
        )
    assert calls == []


@pytest.mark.parametrize(
    "limit,error",
    [("MAX_SPECS", "Too many specs"), ("MAX_CHANNELS", "Too many channels")],
)
def test_workspace_selected_target_limits(rich_manifest, monkeypatch, limit, error):
    monkeypatch.setattr(workspace, limit, 0)
    with pytest.raises(ValueError, match=error):
        WorkspaceInput.from_path(
            rich_manifest, environments=["test"], platforms=["cpu"]
        )


@pytest.mark.parametrize(
    "content,error",
    [
        (
            '[workspace]\nchannels = ["https://user:secret@example.org/channel"]',
            "embedded URL credentials",
        ),
        ("[workspace\n", "Unexpected"),
    ],
)
def test_workspace_errors_hide_credentials_and_server_paths(manifest, content, error):
    path = manifest(content)
    with pytest.raises(ValueError) as raised:
        WorkspaceInput.from_path(path)
    message = str(raised.value)
    assert error.lower() in message.lower()
    assert "secret" not in message
    assert str(path.parent) not in message


def test_workspace_registry_rejects_unknown_filename(manifest):
    assert set(WorkspaceInput.filenames()) == {
        "conda.toml",
        "pixi.toml",
        "pyproject.toml",
    }
    path = manifest("[workspace]", "unknown.toml")
    with pytest.raises(ValueError, match="Unsupported workspace manifest filename"):
        WorkspaceInput.from_path(path)


def test_workspace_combined_lock_preserves_named_targets_and_package_metadata(
    rich_manifest, workspace_solver, restore_solver_context
):
    parsed = WorkspaceInput.from_path(rich_manifest).select(platforms=["cpu", "gpu"])
    body, media_type = parsed.solve("conda-workspaces-lock-v1")
    lock = load_lockfile_data(body)
    assert media_type == "application/yaml"
    assert lock["version"] == 1
    assert set(lock["environments"]) == {"default", "test"}
    packages = {package["conda"]: package for package in lock["packages"]}
    expected_names = {
        ("default", "cpu"): {"python"},
        ("default", "gpu"): {"python"},
        ("test", "cpu"): {"python", "pytest"},
        ("test", "gpu"): {"python", "pytest", "cupy"},
    }
    for name, environment in lock["environments"].items():
        assert set(environment["packages"]) == {"cpu", "gpu"}
        for target, references in environment["packages"].items():
            urls = [reference["conda"] for reference in references]
            assert {url.rsplit("/", 1)[1].split("-")[0] for url in urls} == (
                expected_names[name, target]
            )
            assert all("/linux-64/" in url and url in packages for url in urls)
    python = next(package for url, package in packages.items() if "/python-" in url)
    assert {
        key: python[key]
        for key in (
            "sha256",
            "md5",
            "depends",
            "constrains",
            "features",
            "license",
            "license_family",
            "size",
            "python_site_packages_path",
        )
    } == {
        "sha256": "a" * 64,
        "md5": "b" * 32,
        "depends": ["__glibc >=2.17"],
        "constrains": ["optional >=1"],
        "features": "sample_feature",
        "license": "BSD-3-Clause",
        "license_family": "BSD",
        "size": 123,
        "python_site_packages_path": "lib/python3.13/site-packages",
    }
    calls, _ = workspace_solver
    assert [call["target"] for call in calls] == list(expected_names)
    assert len({call["prefix"] for call in calls}) == 4
    for call in calls:
        assert not call["prefix"].parent.exists()
        assert call["subdir"] == "linux-64"
        assert call["solver"] == "rattler"
        assert call["json"] is True
        if call["target"][1] == "cpu":
            assert call["overrides"]["glibc"] == "2.28"
            assert "cuda" not in call["overrides"]
        else:
            assert call["overrides"]["cuda"] == "12"
        assert "win" not in call["overrides"]


@pytest.mark.parametrize("format_name", [None, "conda-workspaces-lock-v1"])
def test_workspace_failure_restores_target_state_and_never_exports_partial_lock(
    rich_manifest, workspace_solver, restore_solver_context, monkeypatch, format_name
):
    calls, failing = workspace_solver
    failing.add(("test", "gpu"))
    parsed = WorkspaceInput.from_path(
        rich_manifest, environments=["test"], platforms=["gpu", "cpu"]
    )

    def reject_render(*args, **kwargs):
        pytest.fail("An incomplete solve must not reach the exporter")

    monkeypatch.setattr(workspace.OutputFormat, "render", reject_render)
    if format_name is None:
        results = parsed.solve()
        assert [(result.environment, result.platform) for result in results] == [
            ("test", "gpu"),
            ("test", "cpu"),
        ]
        assert "missing-package" in results[0].error
        assert results[0].packages == []
        assert results[1].error is None
        assert {package.name for package in results[1].packages} == {"python", "pytest"}
        assert calls[1]["overrides"]["glibc"] == "2.28"
        assert "cuda" not in calls[1]["overrides"]
    else:
        with pytest.raises(WorkspaceSolveError, match="missing-package") as exc:
            parsed.solve(format_name)
        assert (exc.value.environment, exc.value.platform) == ("test", "gpu")
        assert len(calls) == 1
    assert calls[0]["overrides"]["cuda"] == "12"
    assert all(not call["prefix"].parent.exists() for call in calls)


@pytest.mark.parametrize(
    "environments,platforms,error",
    [
        (["default", "test"], ["cpu"], "Select one environment"),
        (["test"], ["cpu", "gpu"], "targets sharing a conda subdir"),
    ],
)
def test_workspace_export_rejects_unrepresentable_selections_before_solving(
    rich_manifest, workspace_solver, environments, platforms, error
):
    parsed = WorkspaceInput.from_path(
        rich_manifest, environments=environments, platforms=platforms
    )
    with pytest.raises(ValueError, match=error):
        parsed.solve("environment-yaml")
    assert workspace_solver[0] == []


@pytest.mark.parametrize(
    "before,after,environment",
    [
        ("environments.test", "environments.renamed", "renamed"),
        ('cuda = "12"', 'cuda = "13"', "test"),
        ('channel-priority = "strict"', 'channel-priority = "disabled"', "test"),
    ],
    ids=["environment-name", "virtual-requirement", "channel-priority"],
)
def test_workspace_cache_identity_includes_named_target_solve_settings(
    manifest, rich_manifest, restore_solver_context, before, after, environment
):
    original = WorkspaceInput.from_path(
        rich_manifest, environments=["test"], platforms=["gpu"]
    )
    identity = original.cache_identity()
    changed = WorkspaceInput.from_path(
        manifest(rich_manifest.read_text().replace(before, after)),
        environments=[environment],
        platforms=["gpu"],
    )
    assert changed.cache_identity() != identity
    assert original.cache_identity() == identity


@pytest.mark.parametrize("format_name", [None, "conda-workspaces-lock-v1"])
def test_workspace_virtual_root_is_a_requirement_without_becoming_a_package(
    manifest, rich_manifest, workspace_solver, format_name
):
    path = manifest(
        rich_manifest.read_text().replace(
            'python = ">=3.11"', 'python = ">=3.11"\n__glibc = ">=2.17"'
        )
    )
    parsed = WorkspaceInput.from_path(path, environments=["test"], platforms=["cpu"])
    assert "__glibc" in {
        MatchSpec(spec).name for spec in parsed.result.selected[0].specs
    }
    output = parsed.solve(format_name)
    if format_name is None:
        assert output[0].error is None
        assert {package.name for package in output[0].packages} == {"python", "pytest"}
    else:
        lock = load_lockfile_data(output[0])
        urls = {
            reference["conda"]
            for reference in lock["environments"]["test"]["packages"]["cpu"]
        }
        assert {url.rsplit("/", 1)[1].split("-")[0] for url in urls} == {
            "python",
            "pytest",
        }
        assert {package["conda"] for package in lock["packages"]} == urls


@pytest.mark.parametrize(
    "format_name,filename",
    [
        ("conda-toml", "conda.toml"),
        ("pixi-toml", "pixi.toml"),
        ("pyproject-toml", "pyproject.toml"),
    ],
)
@pytest.mark.parametrize("operation", ["solve", "export"])
def test_workspace_normalized_manifest_export_preserves_declared_requirements(
    manifest, rich_manifest, workspace_solver, format_name, filename, operation
):
    parsed = WorkspaceInput.from_path(
        rich_manifest, environments=["test"], platforms=["gpu"]
    )
    body, media_type = getattr(parsed, operation)(format_name)
    exported = WorkspaceInput.from_path(
        manifest(body, filename), platforms=["linux-64"]
    ).result.selected[0]
    assert media_type == "application/toml"
    assert {MatchSpec(spec) for spec in exported.specs} == {
        MatchSpec("python >=3.11"),
        MatchSpec("pytest"),
        MatchSpec("cupy >=13"),
    }
    assert exported.channels == parsed.result.selected[0].channels
    assert exported.subdir == "linux-64"
    assert [call["target"] for call in workspace_solver[0]] == (
        [("test", "gpu")] if operation == "solve" else []
    )


@pytest.mark.parametrize(
    "environments,platforms,format_name,message",
    [
        (["default", "test"], ["cpu"], "conda-toml", "Select one environment"),
        (["test"], ["cpu", "gpu"], "conda-toml", "sharing a conda subdir"),
        (["test"], ["cpu", "osx-arm64"], "environment-yaml", "Select one target"),
    ],
)
def test_workspace_declaration_export_rejects_unrepresentable_selection(
    rich_manifest, workspace_solver, environments, platforms, format_name, message
):
    parsed = WorkspaceInput.from_path(
        rich_manifest, environments=environments, platforms=platforms
    )
    with pytest.raises(ValueError, match=message):
        parsed.export(format_name)
    assert workspace_solver[0] == []


def test_workspace_declaration_export_preserves_multiple_platforms(
    workspace_manifest_path, workspace_solver, manifest
):
    selected = WorkspaceInput.from_path(workspace_manifest_path, environments=["test"])
    body, _ = selected.export("conda-toml")
    exported = WorkspaceInput.from_path(
        manifest(body, "conda.toml"), platforms=["linux-64", "osx-arm64"]
    )
    assert {
        target.subdir: {MatchSpec(spec) for spec in target.specs}
        for target in exported.result.selected
    } == {
        target.subdir: {MatchSpec(spec) for spec in target.specs}
        for target in selected.result.selected
    }
    assert workspace_solver[0] == []
