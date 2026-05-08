"""Tests for conda_presto.cli."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import pytest
import yaml
from conda.base.context import context
from conda.exceptions import PackagesNotFoundError

from conda_presto.cli import (
    cmd_serve,
    execute,
    load_files,
    main,
)


@pytest.fixture()
def run_cli(capsys, monkeypatch):
    """Return a helper that invokes the CLI with the given argv list."""

    def _run(*argv: str) -> str:
        monkeypatch.setattr("sys.argv", ["conda-presto", *argv])
        main()
        return capsys.readouterr().out

    return _run


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


def test_load_files_unhandled(tmp_path, monkeypatch, capsys):
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
        load_files([str(bad)])
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
        lambda app, host, port: called.append((app, host, port)),
    )
    args = argparse.Namespace(host="0.0.0.0", port=9000)
    cmd_serve(args)
    assert called == [("conda_presto.app:app", "0.0.0.0", 9000)]


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
        raise PackagesNotFoundError(["__nonexistent__"])

    monkeypatch.setattr("conda_presto.cli.solve_environments", raise_pnf)
    with pytest.raises(SystemExit, match="1"):
        run_cli(
            "-c",
            "conda-forge",
            "-p",
            "linux-64",
            "--format",
            "explicit",
            "__nonexistent__",
        )
    err = capsys.readouterr().err
    assert "Solver error:" in err
    assert "__nonexistent__" in err
