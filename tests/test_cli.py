"""Tests for conda_presto.cli."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

import msgspec
import pytest
import yaml
from conda.base.context import context
from conda.exceptions import PackagesNotFoundError
from conda.models.match_spec import MatchSpec
from conda.plugins.types import EnvironmentFormat

from conda_presto.cli import (
    cmd_serve,
    execute,
    load_parsed_files,
    main,
)
from conda_presto.config import PARSE_TIMEOUT_S
from conda_presto.exceptions import WorkspaceSolveError
from conda_presto.inputs import ParsedInputFile
from conda_presto.workspace import WorkspaceInput


@pytest.fixture()
def run_cli(capsys, monkeypatch):
    """Return a helper that invokes the CLI with the given argv list."""

    def _run(*argv: str) -> str:
        monkeypatch.setattr("sys.argv", ["conda-presto", *argv])
        main()
        return capsys.readouterr().out

    return _run


def test_parse_simple_file_preserves_response_shape(run_cli, tmp_path):
    path = tmp_path / "environment.yml"
    path.write_text("channels:\n  - conda-forge\ndependencies:\n  - zlib\n")
    result = json.loads(run_cli("--parse", "-f", str(path)))
    assert result == {"specs": ["zlib"], "channels": ["conda-forge"]}


@pytest.mark.parametrize(
    "selectors,environments,platforms,selected_count",
    [
        pytest.param((), None, None, 0, id="discover"),
        pytest.param(
            ("-e", "test", "-e", "default", "-p", "osx-arm64", "-p", "linux-64"),
            ["test", "default"],
            ["osx-arm64", "linux-64"],
            4,
            id="select",
        ),
    ],
)
def test_parse_workspace_matches_bounded_parser(
    run_cli, workspace_manifest_path, selectors, environments, platforms, selected_count
):
    path = workspace_manifest_path
    result = json.loads(run_cli("--parse", "-f", str(path), *selectors))
    parsed = ParsedInputFile.from_content_until(
        path.read_text(),
        path.name,
        platforms,
        time.monotonic() + PARSE_TIMEOUT_S,
        target_environments=environments,
    )
    assert result == msgspec.json.decode(msgspec.json.encode(parsed.parse_result))
    assert len(result["selected"]) == selected_count
    assert str(path.parent) not in json.dumps(result)


@pytest.mark.parametrize(
    "arguments,message,exit_code",
    [
        pytest.param(["--parse"], "exactly one --file", 1, id="missing-file"),
        pytest.param(
            ["--parse", "-f", "one", "-f", "two"],
            "exactly one --file",
            1,
            id="multiple-files",
        ),
        pytest.param(
            ["--parse", "-f", "one", "zlib"],
            "inline package specs",
            1,
            id="inline-specs",
        ),
        pytest.param(
            ["--parse", "-f", "one", "-c", "defaults"],
            "channel overrides",
            1,
            id="channel",
        ),
        pytest.param(
            ["--parse", "-f", "one", "--override-channels"],
            "channel overrides",
            1,
            id="override-channels",
        ),
        pytest.param(
            ["--parse", "-f", "one", "--use-local"],
            "channel overrides",
            1,
            id="local-channel",
        ),
        pytest.param(
            ["--parse", "-f", "one", "--format", "explicit"],
            "does not accept --format",
            1,
            id="format",
        ),
        pytest.param(
            ["--parse", "--serve"], "not allowed with argument", 2, id="serve"
        ),
        pytest.param(["--export"], "exactly one --file", 1, id="export-missing-file"),
        pytest.param(
            ["--export", "-f", "one", "-f", "two", "--format", "explicit"],
            "exactly one --file",
            1,
            id="export-multiple-files",
        ),
        pytest.param(
            ["--export", "-f", "one", "zlib", "--format", "explicit"],
            "inline package specs",
            1,
            id="export-inline-specs",
        ),
        pytest.param(
            ["--export", "-f", "one", "-c", "defaults", "--format", "explicit"],
            "channel overrides",
            1,
            id="export-channel",
        ),
        pytest.param(
            ["--export", "-f", "one", "--override-channels", "--format", "explicit"],
            "channel overrides",
            1,
            id="export-override-channels",
        ),
        pytest.param(
            ["--export", "-f", "one", "--use-local", "--format", "explicit"],
            "channel overrides",
            1,
            id="export-local-channel",
        ),
        pytest.param(
            ["--export", "-f", "one"], "requires --format", 1, id="export-format"
        ),
        pytest.param(
            ["--export", "--parse"], "not allowed with argument", 2, id="export-parse"
        ),
        pytest.param(
            ["--export", "--serve"], "not allowed with argument", 2, id="export-serve"
        ),
        pytest.param(
            ["--environment", "test", "zlib"],
            "Environment selection requires a workspace manifest",
            1,
            id="environment-without-workspace",
        ),
        pytest.param(
            ["--parse", "-f", "conda.lock", "--manifest", "conda.toml"],
            "--manifest requires --export",
            1,
            id="manifest-with-parse",
        ),
        pytest.param(
            ["-f", "conda.lock", "--manifest", "conda.toml"],
            "--manifest requires --export",
            1,
            id="manifest-with-solve",
        ),
        pytest.param(
            ["--serve", "--manifest", "conda.toml"],
            "--manifest requires --export",
            1,
            id="manifest-with-serve",
        ),
        pytest.param(
            ["--validate"], "exactly one --file", 2, id="validate-missing-file"
        ),
        pytest.param(
            ["--validate", "-f", "one", "-f", "two", "--manifest", "conda.toml"],
            "exactly one --file",
            2,
            id="validate-multiple-files",
        ),
        pytest.param(
            ["--validate", "-f", "conda.lock"],
            "requires --manifest",
            2,
            id="validate-missing-manifest",
        ),
        pytest.param(
            ["--validate", "-f", "conda.lock", "--manifest", "conda.toml", "zlib"],
            "inline package specs",
            2,
            id="validate-inline-specs",
        ),
        *[
            pytest.param(
                [
                    "--validate",
                    "-f",
                    "conda.lock",
                    "--manifest",
                    "conda.toml",
                    *option,
                ],
                message,
                2,
                id=f"validate-{name}",
            )
            for name, option, message in (
                ("channel", ["-c", "defaults"], "channel overrides"),
                ("override-channels", ["--override-channels"], "channel overrides"),
                ("local-channel", ["--use-local"], "channel overrides"),
                ("format", ["--format", "explicit"], "does not accept --format"),
                ("platform", ["-p", "cpu"], "does not accept --platform"),
                ("environment", ["-e", "test"], "does not accept --environment"),
                ("parse", ["--parse"], "not allowed with argument"),
                ("export", ["--export"], "not allowed with argument"),
                ("serve", ["--serve"], "not allowed with argument"),
            )
        ],
    ],
)
def test_parse_rejects_incompatible_arguments(
    run_cli, capsys, arguments, message, exit_code
):
    with pytest.raises(SystemExit) as exc:
        run_cli(*arguments)
    assert exc.value.code == exit_code
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "mode,exit_code",
    [("--parse", 1), ("--export", 1), ("--validate", 2)],
)
def test_parse_timeout_exits_cleanly(
    run_cli,
    workspace_manifest_path,
    workspace_lock_path,
    monkeypatch,
    capsys,
    mode,
    exit_code,
):
    monkeypatch.setattr("conda_presto.cli.PARSE_TIMEOUT_S", 0)
    extra = (
        ["--manifest", str(workspace_manifest_path)]
        if mode == "--validate"
        else ["--format", "explicit"]
        if mode == "--export"
        else []
    )
    with pytest.raises(SystemExit) as exc:
        run_cli(mode, "-f", str(workspace_lock_path), *extra)
    assert exc.value.code == exit_code
    assert "Parse exceeded 0s timeout" in capsys.readouterr().err


@pytest.mark.parametrize(
    "filename,content,message",
    [
        pytest.param("conda.toml", "[workspace\n", "Input error:", id="invalid-toml"),
        pytest.param(
            "environment.yml", b"\xff", "not valid UTF-8", id="invalid-encoding"
        ),
        pytest.param("missing.toml", None, "Cannot read input file", id="missing-file"),
    ],
)
@pytest.mark.parametrize(
    "mode,exit_code",
    [("--parse", 1), ("--export", 1), ("--validate", 2)],
)
def test_parse_reports_file_errors(
    run_cli,
    tmp_path,
    capsys,
    workspace_manifest_path,
    filename,
    content,
    message,
    mode,
    exit_code,
):
    path = tmp_path / filename
    if content is not None:
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    extra = (
        ["--manifest", str(workspace_manifest_path)]
        if mode == "--validate"
        else ["--format", "explicit"]
        if mode == "--export"
        else []
    )
    with pytest.raises(SystemExit) as exc:
        run_cli(mode, "-f", str(path), *extra)
    assert exc.value.code == exit_code
    error = capsys.readouterr().err
    assert message in error
    assert str(tmp_path) not in error
    assert "Traceback" not in error


@pytest.mark.parametrize("with_manifest", [False, True])
def test_export_preserves_rendered_bytes_and_passes_selection(
    run_cli, tmp_path, monkeypatch, with_manifest
):
    path = tmp_path / "conda.lock"
    path.write_text("source lockfile")
    manifest = tmp_path / "conda.toml"
    manifest.write_text("source manifest")
    rendered = '# exact export\n  package: "\u03bb"  \n\n'
    format_name = "cyclonedx-json-v1.7" if with_manifest else "conda-workspaces-lock-v1"

    def parse(content, filename, platforms, deadline, **kwargs):
        assert content == "source lockfile"
        assert filename == "conda.lock"
        assert platforms == ["gpu"]
        assert deadline > time.monotonic()
        expected = {
            "export_format": format_name,
            "target_environments": ["test"],
        }
        if with_manifest:
            expected.update(
                manifest_content="source manifest", manifest_filename="conda.toml"
            )
        assert kwargs == expected
        return ParsedInputFile(
            specs=[],
            channels=[],
            environment_format=EnvironmentFormat.lockfile,
            source_format="conda-workspaces-lock-v1",
            exported_content=rendered,
        )

    monkeypatch.setattr(ParsedInputFile, "from_content_until", parse)
    assert (
        run_cli(
            "--export",
            "-f",
            str(path),
            "-e",
            "test",
            "-p",
            "gpu",
            "--format",
            format_name,
            *(["--manifest", str(manifest)] if with_manifest else []),
        )
        == rendered
    )


def test_export_rejects_unresolved_explicit_output(
    run_cli, environment_yml_path, capsys
):
    with pytest.raises(SystemExit, match="1"):
        run_cli("--export", "-f", str(environment_yml_path), "--format", "explicit")
    assert "requires solved package records" in capsys.readouterr().err


@pytest.mark.parametrize(
    "filename", ["environment.yml", "requirements.txt", "conda.toml"]
)
def test_export_declarations_preserves_requirements(run_cli, tmp_path, filename):
    path = tmp_path / filename
    content = {
        "environment.yml": "channels: [conda-forge]\ndependencies: ['zlib >=1']\n",
        "requirements.txt": "zlib >=1\n",
        "conda.toml": (
            '[workspace]\nchannels = ["conda-forge"]\nplatforms = ["linux-64"]\n'
            '[dependencies]\nzlib = ">=1"\n'
        ),
    }[filename]
    path.write_text(content)
    exported = json.loads(
        run_cli("--export", "-f", str(path), "--format", "environment-json")
    )
    assert [MatchSpec(spec) for spec in exported["dependencies"]] == [
        MatchSpec("zlib >=1")
    ]


def test_export_reports_unknown_format(run_cli, environment_yml_path, capsys):
    with pytest.raises(SystemExit, match="1"):
        run_cli("--export", "-f", str(environment_yml_path), "--format", "unknown")
    assert "Unknown format 'unknown'" in capsys.readouterr().err


@pytest.mark.parametrize(
    "selectors,selected",
    [
        pytest.param([], [], id="discover"),
        pytest.param(
            ["-e", "test", "-p", "cpu"],
            [{"environment": "test", "platform": "cpu", "subdir": "linux-64"}],
            id="select-logical-target",
        ),
    ],
)
def test_parse_workspace_lock_discovers_named_targets(
    run_cli, workspace_lock_path, selectors, selected
):
    result = json.loads(run_cli("--parse", "-f", str(workspace_lock_path), *selectors))
    assert result["format"] == "conda-workspaces-lock-v1"
    assert {environment["name"] for environment in result["environments"]} == {
        "default",
        "test",
    }
    assert result["selected"] == selected
    assert str(workspace_lock_path.parent) not in json.dumps(result)


@pytest.mark.parametrize("format_name", ["conda-workspaces-lock-v1", "workspace-lock"])
@pytest.mark.parametrize("selectors", [(), ("-e", "test", "-p", "cpu")])
def test_export_workspace_lock_preserves_selected_records(
    run_cli, workspace_lock_path, workspace_lock_data, format_name, selectors
):
    output = run_cli(
        "--export",
        "-f",
        str(workspace_lock_path),
        *selectors,
        "--format",
        format_name,
    )
    lock = yaml.safe_load(output)
    if not selectors:
        assert lock == workspace_lock_data
    else:
        assert set(lock["environments"]) == {"test"}
        expected = workspace_lock_data["environments"]["test"]
        assert lock["environments"]["test"] == {
            **expected,
            "packages": {"cpu": expected["packages"]["cpu"]},
        }
        assert lock["packages"] == workspace_lock_data["packages"][:1]
        assert lock["metadata"] == workspace_lock_data["metadata"]


def test_export_workspace_lock_uses_explicit_exporter(
    run_cli, workspace_lock_path, workspace_lock_data
):
    output = run_cli(
        "--export",
        "-f",
        str(workspace_lock_path),
        "-e",
        "test",
        "-p",
        "cpu",
        "--format",
        "explicit",
    )
    assert "@EXPLICIT" in output.splitlines()
    assert output.split("@EXPLICIT\n", 1)[1].splitlines() == [
        reference["conda"]
        for reference in workspace_lock_data["environments"]["test"]["packages"]["cpu"]
    ]


@pytest.fixture
def workspace_sbom_inputs(tmp_path, workspace_lock_path, workspace_lock_data):
    workspace_lock_data["packages"][0]["depends"] = ["gpu-probe >=1"]
    workspace_lock_data["environments"]["test"]["packages"]["cpu"].append(
        {"conda": workspace_lock_data["packages"][1]["conda"]}
    )
    workspace_lock_path.write_text(yaml.safe_dump(workspace_lock_data))
    manifest = tmp_path / "conda.toml"
    manifest.write_text(
        '[workspace]\nchannels = ["conda-forge"]\n'
        'platforms = [{name = "cpu", platform = "linux-64"}]\n'
        '[dependencies]\nprobe = ">=1"\ngpu-probe = "*"\n'
        "[environments]\ntest = []\n"
    )
    return workspace_lock_path, manifest


@pytest.mark.parametrize("with_manifest", [False, True])
def test_workspace_sbom_preserves_records_and_distinguishes_declared_roots(
    run_cli,
    workspace_sbom_inputs,
    workspace_lock_data,
    tmp_path,
    monkeypatch,
    with_manifest,
):
    lock, manifest = workspace_sbom_inputs
    package_cache = tmp_path / "package-cache"
    package_cache.mkdir()
    monkeypatch.setenv("CONDA_PKGS_DIRS", str(package_cache))
    monkeypatch.setenv("CONDA_OFFLINE", "true")
    document = json.loads(
        run_cli(
            "--export",
            "-f",
            str(lock),
            "-e",
            "test",
            "-p",
            "cpu",
            "--format",
            "cyclonedx-json-v1.7",
            *(["--manifest", str(manifest)] if with_manifest else []),
        )
    )
    root = document["metadata"]["component"]
    properties = {item["name"]: item["value"] for item in root["properties"]}
    assert properties["conda:environment:root-dependency-source"] == (
        "requested-packages" if with_manifest else "inferred-graph-roots"
    )
    components = {item["name"]: item for item in document["components"]}
    assert set(components) == {"probe", "gpu-probe"}
    for component in components.values():
        assert component["version"] == "1.0"
        assert {item["alg"]: item["content"] for item in component["hashes"]} == {
            "SHA-256": "a" * 64,
            "MD5": "b" * 32,
        }
        assert {item["name"]: item["value"] for item in component["properties"]}[
            "conda:package:build-number"
        ] == "7"
    assert {
        reference["url"]
        for component in components.values()
        for reference in component["externalReferences"]
        if reference["type"] == "distribution"
    } == {
        reference["conda"]
        for reference in workspace_lock_data["environments"]["test"]["packages"]["cpu"]
    }
    dependencies = {item["ref"]: item["dependsOn"] for item in document["dependencies"]}
    assert set(dependencies[root["bom-ref"]]) == {
        components[name]["bom-ref"]
        for name in (components if with_manifest else ["probe"])
    }
    assert dependencies[components["probe"]["bom-ref"]] == [
        components["gpu-probe"]["bom-ref"]
    ]
    assert not any(path.is_file() for path in package_cache.rglob("*"))


def test_workspace_sbom_rejects_mismatched_companion_manifest(
    run_cli, workspace_sbom_inputs, capsys
):
    lock, manifest = workspace_sbom_inputs
    manifest.write_text(manifest.read_text().replace('probe = ">=1"', 'probe = ">=2"'))
    with pytest.raises(SystemExit, match="1"):
        run_cli(
            "--export",
            "-f",
            str(lock),
            "-e",
            "test",
            "-p",
            "cpu",
            "--format",
            "cyclonedx-json-v1.7",
            "--manifest",
            str(manifest),
        )
    assert "manifest" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "selectors,message",
    [
        ([], "Select one environment"),
        (["-e", "test", "-p", "cpu", "-p", "osx-arm64"], "Select one target"),
    ],
)
def test_workspace_sbom_requires_one_environment_and_target(
    run_cli, workspace_lock_path, capsys, selectors, message
):
    with pytest.raises(SystemExit, match="1"):
        run_cli(
            "--export",
            "-f",
            str(workspace_lock_path),
            "--format",
            "cyclonedx-json-v1.7",
            *selectors,
        )
    assert message in capsys.readouterr().err


def test_companion_manifest_requires_workspace_lock_input(
    run_cli, environment_yml_path, workspace_manifest_path, capsys
):
    with pytest.raises(SystemExit, match="1"):
        run_cli(
            "--export",
            "-f",
            str(environment_yml_path),
            "--format",
            "environment-yaml",
            "--manifest",
            str(workspace_manifest_path),
        )
    assert "require conda.lock" in capsys.readouterr().err


@pytest.mark.parametrize(
    "content,message", [(None, "Cannot read input file"), (b"\xff", "not valid UTF-8")]
)
@pytest.mark.parametrize("mode,exit_code", [("--export", 1), ("--validate", 2)])
def test_parse_reports_companion_manifest_file_errors(
    run_cli, workspace_lock_path, tmp_path, capsys, content, message, mode, exit_code
):
    manifest = tmp_path / "conda.toml"
    if content is not None:
        manifest.write_bytes(content)
    with pytest.raises(SystemExit) as exc:
        run_cli(
            mode,
            "-f",
            str(workspace_lock_path),
            *(["--format", "cyclonedx-json-v1.7"] if mode == "--export" else []),
            "--manifest",
            str(manifest),
        )
    assert exc.value.code == exit_code
    error = capsys.readouterr().err
    assert message in error
    assert str(tmp_path) not in error


@pytest.mark.parametrize("consistent", [True, False], ids=["consistent", "mismatch"])
def test_validate_reports_all_targets_without_changing_inputs(
    run_cli,
    workspace_consistent_lock_path,
    workspace_consistent_manifest_text,
    tmp_path,
    capsys,
    monkeypatch,
    consistent,
):
    manifest = tmp_path / "conda.toml"
    manifest.write_text(workspace_consistent_manifest_text)
    if not consistent:
        workspace_consistent_lock_path.write_text(
            workspace_consistent_lock_path.read_text().replace(
                "/linux-64/probe-1.0-", "/linux-64/probe-2.0-"
            )
        )
    lock_before = workspace_consistent_lock_path.read_bytes()
    manifest_before = manifest.read_bytes()
    package_cache = tmp_path / "package-cache"
    package_cache.mkdir()
    monkeypatch.setenv("CONDA_PKGS_DIRS", str(package_cache))
    monkeypatch.setenv("CONDA_OFFLINE", "true")

    def unexpected_solve(*args, **kwargs):
        pytest.fail("Workspace lock check reached the solver")

    monkeypatch.setattr("conda_presto.cli.solve", unexpected_solve)
    monkeypatch.setattr("conda_presto.cli.solve_environments", unexpected_solve)
    arguments = (
        "--validate",
        "-f",
        str(workspace_consistent_lock_path),
        "--manifest",
        str(manifest),
    )
    if consistent:
        output = run_cli(*arguments)
    else:
        with pytest.raises(SystemExit) as exc:
            run_cli(*arguments)
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert not captured.err
        output = captured.out
    report = json.loads(output)
    assert report["consistent"] is consistent
    assert len(report["targets"]) == 6
    targets = {
        (target["environment"], target["platform"]): target
        for target in report["targets"]
    }
    for environment in ("default", "test"):
        for platform, subdir in (
            ("cpu", "linux-64"),
            ("gpu", "linux-64"),
            ("osx-arm64", "osx-arm64"),
        ):
            target = targets[environment, platform]
            assert target["subdir"] == subdir
            assert target["consistent"] is (consistent or platform != "cpu")
            assert (target["reason"] is None) is target["consistent"]
    assert workspace_consistent_lock_path.read_bytes() == lock_before
    assert manifest.read_bytes() == manifest_before
    assert not any(path.is_file() for path in package_cache.rglob("*"))


@pytest.mark.parametrize("arguments", [[], ["--format", "conda-workspaces-lock-v1"]])
def test_workspace_lock_requires_explicit_export_mode(
    run_cli, workspace_lock_path, monkeypatch, capsys, arguments
):
    def unexpected_solve(*args, **kwargs):
        pytest.fail("Workspace lockfile reached the solver")

    monkeypatch.setattr("conda_presto.cli.solve", unexpected_solve)
    monkeypatch.setattr("conda_presto.cli.solve_environments", unexpected_solve)
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(workspace_lock_path), *arguments)
    assert "--export with --format" in capsys.readouterr().err


@pytest.mark.parametrize(
    "selectors,expected",
    [
        pytest.param(
            [],
            [
                ("default", "linux-64"),
                ("default", "osx-arm64"),
                ("test", "linux-64"),
                ("test", "osx-arm64"),
            ],
            id="all-environments",
        ),
        pytest.param(
            ["-e", "test", "-p", "osx-arm64"],
            [("test", "osx-arm64")],
            id="selected-environment",
        ),
    ],
)
@pytest.mark.parametrize("output_format", [None, "conda-workspaces-lock-v1"])
def test_workspace_solve_uses_selected_targets(
    run_cli, workspace_manifest_path, monkeypatch, selectors, expected, output_format
):
    calls = []

    def validate(self, format_name):
        calls.append(("validate", format_name))

    def solve(self, format_name=None):
        pairs = [
            (target.environment, target.platform) for target in self.result.selected
        ]
        calls.append(("solve", format_name, pairs))
        if format_name is not None:
            return "locked output\n", "application/yaml"
        return [
            {
                "environment": target.environment,
                "platform": target.platform,
                "subdir": target.subdir,
                "packages": [],
                "error": None,
            }
            for target in self.result.selected
        ]

    monkeypatch.setattr(WorkspaceInput, "validate_output", validate)
    monkeypatch.setattr(WorkspaceInput, "solve", solve)
    arguments = ["-f", str(workspace_manifest_path), *selectors]
    if output_format is not None:
        arguments.extend(["--format", output_format])
    output = run_cli(*arguments)
    assert calls == [
        ("validate", output_format),
        ("solve", output_format, expected),
    ]
    if output_format is not None:
        assert output == "locked output\n"
    else:
        assert [
            (row["environment"], row["platform"]) for row in json.loads(output)
        ] == expected


@pytest.mark.parametrize(
    "arguments,message",
    [
        pytest.param(["zlib"], "inline package specs", id="inline-specs"),
        pytest.param(["-c", "defaults"], "channel overrides", id="channel"),
        pytest.param(["--override-channels"], "channel overrides", id="override"),
        pytest.param(["--use-local"], "channel overrides", id="local"),
        pytest.param(["-f", "WORKSPACE"], "exactly one --file", id="multiple-files"),
    ],
)
def test_workspace_solve_rejects_mixed_inputs(
    run_cli, workspace_manifest_path, monkeypatch, capsys, arguments, message
):
    def unexpected_solve(*args, **kwargs):
        pytest.fail("Rejected workspace input reached the solver")

    monkeypatch.setattr(WorkspaceInput, "solve", unexpected_solve)
    arguments = [
        str(workspace_manifest_path) if argument == "WORKSPACE" else argument
        for argument in arguments
    ]
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(workspace_manifest_path), *arguments)
    assert message in capsys.readouterr().err


def test_workspace_output_validation_precedes_solving(
    run_cli, workspace_manifest_path, monkeypatch, capsys
):
    def reject(self, format_name):
        raise ValueError("This exporter requires one environment")

    def unexpected_solve(*args, **kwargs):
        pytest.fail("Unsupported output reached the solver")

    monkeypatch.setattr(WorkspaceInput, "validate_output", reject)
    monkeypatch.setattr(WorkspaceInput, "solve", unexpected_solve)
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(workspace_manifest_path), "--format", "environment-yaml")
    assert "This exporter requires one environment" in capsys.readouterr().err


@pytest.mark.parametrize(
    "selectors,message",
    [
        pytest.param(
            ["-e", "missing"], "Unknown workspace environment", id="environment"
        ),
        pytest.param(["-p", "win-64"], "Unknown workspace platform", id="platform"),
    ],
)
def test_workspace_solve_preserves_selection_errors(
    run_cli, workspace_manifest_path, capsys, selectors, message
):
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(workspace_manifest_path), *selectors)
    error = capsys.readouterr().err
    assert message in error
    assert "No environment spec plugin" not in error


def test_workspace_solve_preserves_malformed_manifest_error(run_cli, tmp_path, capsys):
    path = tmp_path / "conda.toml"
    path.write_text("[workspace\n")
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(path))
    error = capsys.readouterr().err
    assert "Input error:" in error
    assert "No environment spec plugin" not in error
    assert str(tmp_path) not in error
    assert "Traceback" not in error


def test_workspace_solve_reports_failed_pair(
    run_cli, workspace_manifest_path, monkeypatch, capsys
):
    def fail(self, format_name=None):
        raise WorkspaceSolveError("test", "linux-64", "Packages unavailable")

    monkeypatch.setattr(WorkspaceInput, "solve", fail)
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(workspace_manifest_path), "-e", "test", "-p", "linux-64")
    error = capsys.readouterr().err
    assert "Environment 'test' on 'linux-64': Packages unavailable" in error
    assert "Traceback" not in error


def test_environment_selection_requires_workspace_input(
    run_cli, environment_yml_path, capsys
):
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(environment_yml_path), "-e", "test")
    assert (
        "Environment selection requires a workspace manifest" in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "extra_args, expected_name",
    [
        pytest.param([], "python", id="file-only"),
        pytest.param(["-c", "conda-forge"], "python", id="file-with-channel"),
    ],
)
def test_solve_with_file(run_cli, environment_yml_path, extra_args, expected_name):
    out = run_cli("-f", str(environment_yml_path), "-p", "linux-64", *extra_args)
    data = json.loads(out)
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["platform"] == "linux-64"
    assert expected_name in [p["name"] for p in data[0]["packages"]]


def test_solve_with_inline_specs(run_cli):
    out = run_cli("-c", "conda-forge", "-p", "linux-64", "zlib")
    data = json.loads(out)
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["platform"] == "linux-64"
    names = [p["name"] for p in data[0]["packages"]]
    assert "zlib" in names
    pkg = data[0]["packages"][0]
    assert "sha256" in pkg
    assert "url" in pkg
    assert "channel" in pkg


def test_solve_no_args_exits(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["conda-presto"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "Provide an environment file" in err


def test_solve_format_no_args_exits(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["conda-presto", "--format", "explicit"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "Provide an environment file" in err


@pytest.mark.parametrize(
    "fmt, assertion",
    [
        pytest.param("explicit", "@EXPLICIT", id="explicit"),
        pytest.param("yaml", "dependencies:", id="yaml"),
    ],
)
def test_solve_output_format(run_cli, fmt, assertion):
    out = run_cli("-c", "conda-forge", "-p", "linux-64", "--format", fmt, "zlib")
    assert assertion in out


@pytest.mark.crossplatform
def test_solve_multi_platform_default_is_single_array(run_cli):
    """Multi-platform CLI solve emits a single JSON array of SolveResult
    structs, byte-identical to what the HTTP API returns."""
    out = run_cli(
        "-c",
        "conda-forge",
        "-p",
        "linux-64",
        "-p",
        "osx-arm64",
        "zlib",
    )
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 2
    platforms = {entry["platform"] for entry in data}
    assert platforms == {"linux-64", "osx-arm64"}
    for entry in data:
        assert entry["error"] is None
        names = [p["name"] for p in entry["packages"]]
        assert "zlib" in names


@pytest.mark.parametrize(
    "fmt, version_key, version_value, package_marker",
    [
        pytest.param("pixi-lock-v6", "version", 6, "zlib-", id="pixi-lock-v6"),
        pytest.param("conda-lock-v1", "version", 1, "zlib", id="conda-lock-v1"),
    ],
)
def test_convert_environment_yml_structural(
    run_cli, tmp_path, fmt, version_key, version_value, package_marker
):
    """``environment.yml`` -> solve -> lockfile structural sanity check."""
    env_yml = tmp_path / "environment.yml"
    env_yml.write_text("channels:\n  - conda-forge\ndependencies:\n  - zlib\n")

    out = run_cli("-f", str(env_yml), "-p", "linux-64", "--format", fmt)

    data = yaml.safe_load(out)
    assert data[version_key] == version_value
    assert package_marker in out


@pytest.mark.parametrize(
    "mode,output_format",
    [
        pytest.param((), "conda-lock-v1", id="legacy-transcode"),
        pytest.param(("--export",), "pixi-lock-v6", id="explicit-export-mode"),
    ],
)
def test_lockfile_to_lockfile_transcodes_without_solver(
    run_cli, tmp_path, monkeypatch, pixi_lock_v6_text, mode, output_format
):
    lock = tmp_path / "pixi.lock"
    lock.write_text(pixi_lock_v6_text)

    def fail_solve(*args, **kwargs):
        raise AssertionError("solver should not run")

    monkeypatch.setattr("conda_presto.cli.solve_environments", fail_solve)
    out = run_cli(*mode, "-f", str(lock), "-p", "linux-64", "--format", output_format)

    data = yaml.safe_load(out)
    if output_format == "conda-lock-v1":
        assert data["version"] == 1
        assert data["metadata"]["platforms"] == ["linux-64"]
        assert {pkg["name"] for pkg in data["package"]} == {"libzlib", "zlib"}
    else:
        original = yaml.safe_load(pixi_lock_v6_text)
        assert data["version"] == 6
        assert set(data["environments"]["default"]["packages"]) == {"linux-64"}
        assert data["packages"] == original["packages"]


def test_pipeline_environment_yml_to_conda_env_create(tmp_path):
    """Shell-style one-liner pipeline, end to end:

    ``environment.yml`` --[``conda presto --format pixi-lock-v6``]-->
    ``pixi.lock`` --[``conda env create --dry-run``]--> solved env.

    Proves that a pixi.lock produced by conda-presto is consumable by
    conda's env-spec plugin registry (via ``conda-lockfiles``) without
    touching any plugin internals. Exercises only conda's public CLI.
    """
    env_yml = tmp_path / "environment.yml"
    env_yml.write_text("channels:\n  - conda-forge\ndependencies:\n  - zlib\n")
    lock = tmp_path / "pixi.lock"

    with lock.open("w") as f:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "conda",
                "presto",
                "-f",
                str(env_yml),
                "-p",
                context.subdir,
                "--format",
                "pixi-lock-v6",
            ],
            stdout=f,
            check=True,
        )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "conda",
            "env",
            "create",
            "--dry-run",
            "--yes",
            "-n",
            "conda_presto_pipeline_demo",
            "-f",
            str(lock),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "zlib" in result.stdout


def test_solve_multiple_files(run_cli, tmp_path):
    file1 = tmp_path / "env1.yml"
    file1.write_text("name: a\nchannels:\n  - conda-forge\ndependencies:\n  - zlib\n")
    file2 = tmp_path / "env2.yml"
    file2.write_text("name: b\nchannels:\n  - conda-forge\ndependencies:\n  - bzip2\n")
    out = run_cli("-f", str(file1), "-f", str(file2), "-p", "linux-64")
    data = json.loads(out)
    assert isinstance(data, list) and len(data) == 1
    names = [p["name"] for p in data[0]["packages"]]
    assert "zlib" in names
    assert "bzip2" in names


def test_load_parsed_files_unhandled(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "env.yml"
    bad.write_text("name: test\nchannels:\n  - conda-forge\ndependencies:\n  - zlib\n")

    class FakeSpec:
        def can_handle(self):
            return False

    class FakePlugin:
        def environment_spec(self, filename):
            return FakeSpec()

    monkeypatch.setattr(
        "conda_presto.cli.context.plugin_manager.detect_environment_specifier",
        lambda fpath: FakePlugin(),
    )
    with pytest.raises(SystemExit, match="1"):
        load_parsed_files([str(bad)])
    assert "No conda environment spec plugin can handle" in capsys.readouterr().err


def test_execute_serve_branch(monkeypatch):
    called = []
    monkeypatch.setattr(
        "conda_presto.cli.cmd_serve",
        lambda args: called.append(args),
    )
    args = argparse.Namespace(serve=True, host="127.0.0.1", port=8000)
    execute(args)
    assert len(called) == 1


def test_cmd_serve(monkeypatch):
    called = []
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, host, port, access_log: called.append(
            (app, host, port, access_log)
        ),
    )
    args = argparse.Namespace(host="0.0.0.0", port=9000)
    cmd_serve(args)
    assert called == [("conda_presto.app:app", "0.0.0.0", 9000, False)]


def test_cmd_solve_unknown_format_exits(run_cli, monkeypatch, capsys):
    """cmd_solve surfaces an UnknownFormatError as exit 1 + stderr."""
    monkeypatch.setattr(
        "conda_presto.cli.solve_environments",
        lambda channels, deps, platforms: [],
    )
    with pytest.raises(SystemExit, match="1"):
        run_cli(
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--format",
            "no-such-format",
            "zlib",
        )
    assert "Unknown format 'no-such-format'" in capsys.readouterr().err


def test_cmd_solve_format_solver_error_exits(run_cli, monkeypatch, capsys):
    """cmd_solve --format surfaces known solver errors cleanly (no traceback)."""

    def raise_pnf(*a, **kw):
        raise PackagesNotFoundError(
            ["nonexistent-package-zzzzzz"],
            ["https://user:password@example.test/t/private/channel"],
        )

    monkeypatch.setattr("conda_presto.cli.solve_environments", raise_pnf)
    with pytest.raises(SystemExit, match="1"):
        run_cli(
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--format",
            "explicit",
            "nonexistent-package-zzzzzz",
        )
    err = capsys.readouterr().err
    assert "Solver error:" in err
    assert "nonexistent-package-zzzzzz" in err
    assert "Current channels: [redacted]" in err
    assert "user" not in err
    assert "password" not in err
    assert "private" not in err
