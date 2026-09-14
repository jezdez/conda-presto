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

from conda_presto.cli import (
    cmd_serve,
    execute,
    load_parsed_files,
    main,
)
from conda_presto.config import PARSE_TIMEOUT_S
from conda_presto.inputs import ParsedInputFile


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
        pytest.param(
            ["--environment", "test", "zlib"],
            "--environment requires --parse",
            1,
            id="environment-without-parse",
        ),
    ],
)
def test_parse_rejects_incompatible_arguments(
    run_cli, capsys, arguments, message, exit_code
):
    with pytest.raises(SystemExit) as exc:
        run_cli(*arguments)
    assert exc.value.code == exit_code
    assert message in capsys.readouterr().err


def test_parse_timeout_exits_cleanly(
    run_cli, workspace_manifest_path, monkeypatch, capsys
):
    monkeypatch.setattr("conda_presto.cli.PARSE_TIMEOUT_S", 0)
    with pytest.raises(SystemExit, match="1"):
        run_cli("--parse", "-f", str(workspace_manifest_path))
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
def test_parse_reports_file_errors(
    run_cli, tmp_path, capsys, filename, content, message
):
    path = tmp_path / filename
    if content is not None:
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    with pytest.raises(SystemExit, match="1"):
        run_cli("--parse", "-f", str(path))
    error = capsys.readouterr().err
    assert message in error
    assert str(tmp_path) not in error
    assert "Traceback" not in error


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="native"),
        pytest.param(["zlib"], id="extra-specs"),
        pytest.param(["--format", "environment-yaml"], id="exporter"),
    ],
)
def test_workspace_solve_is_rejected_before_solver(
    run_cli, workspace_manifest_path, extra_args, monkeypatch, capsys
):
    def unexpected_solve(*args, **kwargs):
        pytest.fail("Workspace input reached the solver")

    monkeypatch.setattr("conda_presto.cli.solve", unexpected_solve)
    monkeypatch.setattr("conda_presto.cli.solve_environments", unexpected_solve)
    with pytest.raises(SystemExit, match="1"):
        run_cli("-f", str(workspace_manifest_path), *extra_args)
    assert "Workspace solving is not supported yet" in capsys.readouterr().err


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


def test_lockfile_to_lockfile_transcodes_without_solver(
    run_cli, tmp_path, monkeypatch, pixi_lock_v6_text
):
    lock = tmp_path / "pixi.lock"
    lock.write_text(pixi_lock_v6_text)

    def fail_solve(*args, **kwargs):
        raise AssertionError("solver should not run")

    monkeypatch.setattr("conda_presto.cli.solve_environments", fail_solve)
    out = run_cli("-f", str(lock), "-p", "linux-64", "--format", "conda-lock-v1")

    data = yaml.safe_load(out)
    assert data["version"] == 1
    assert data["metadata"]["platforms"] == ["linux-64"]
    assert {pkg["name"] for pkg in data["package"]} == {"libzlib", "zlib"}


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
    assert "No environment spec plugin can handle" in capsys.readouterr().err


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
