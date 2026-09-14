"""Workspace manifest discovery and selection behavior."""

from __future__ import annotations

import os
import textwrap

import msgspec
import pytest
from conda.base.context import context
from conda.models.match_spec import MatchSpec
from conda_workspaces.resolver import ResolvedEnvironment

from conda_presto import workspace
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
        ("[workspace]\nchannels = [42]", "Workspace channels"),
        ('[feature.test]\nchannels = "conda-forge"', "Workspace channels"),
        ("[environments.test]\nchannels = [{}]", "Workspace channels"),
        ("[target.linux-64]\nchannels = [{ channel = 42 }]", "Workspace channels"),
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
def test_workspace_rejects_conda_source_fields_before_provider_omits_them(
    manifest, source
):
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
