"""Shared declaration exports use parsed environments without solving."""

from __future__ import annotations

import json

import pytest
from conda.core.package_cache_data import ProgressiveFetchExtract
from conda.exceptions import CondaError
from conda.models.match_spec import MatchSpec
from conda_workspaces.resolver import ResolvedEnvironment

from conda_presto.inputs import ParsedInputFile


@pytest.fixture
def declaration_path(request, tmp_path):
    filename, content = request.param
    path = tmp_path / filename
    path.write_text(content)
    return path


@pytest.fixture
def no_declaration_solve(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Declaration export tried to solve or fetch packages")

    monkeypatch.setattr(ResolvedEnvironment, "solve_for_platform", fail)
    monkeypatch.setattr(ProgressiveFetchExtract, "execute", fail)


@pytest.mark.parametrize(
    "declaration_path",
    [
        (
            "environment.yml",
            "name: demo\nchannels: [conda-forge]\ndependencies: ['zlib >=1']\n",
        ),
        ("requirements.txt", "zlib >=1\n"),
    ],
    indirect=True,
)
def test_declared_inputs_export_specifier_requirements(
    declaration_path, no_declaration_solve
):
    parsed = ParsedInputFile.from_path(
        declaration_path, export_format="environment-json"
    )
    exported = json.loads(parsed.exported_content)
    assert [MatchSpec(spec) for spec in exported["dependencies"]] == [
        MatchSpec("zlib >=1")
    ]
    assert not parsed.is_lockfile
    assert not parsed.environments[0].explicit_packages


@pytest.mark.parametrize(
    "format_name",
    ["explicit", "workspace-lock", "conda-lock-v1", "pixi-lock-v6", "cyclonedx"],
)
@pytest.mark.parametrize("filename", ["environment.yml", "conda.toml"])
def test_declared_inputs_reject_resolved_output(
    tmp_path, no_declaration_solve, format_name, filename
):
    path = tmp_path / filename
    path.write_text(
        '[workspace]\nplatforms = ["linux-64"]\n[dependencies]\nzlib = "*"\n'
        if filename == "conda.toml"
        else "dependencies: [zlib]\n"
    )
    with pytest.raises(
        (CondaError, ValueError), match="(?:solved package|exact package) records"
    ):
        ParsedInputFile.from_path(path, export_format=format_name)


def test_ordinary_declaration_export_rejects_platform_selection(
    environment_yml_path, no_declaration_solve
):
    with pytest.raises(ValueError, match="Platform selection requires a workspace"):
        ParsedInputFile.from_path(
            environment_yml_path, ["linux-64"], export_format="environment-yaml"
        )


@pytest.mark.parametrize("filename", ["environment.yml", "conda.toml"])
def test_lockfile_only_parsing_does_not_render_declarations(
    filename, tmp_path, workspace_manifest_text, no_declaration_solve
):
    path = tmp_path / filename
    path.write_text(
        workspace_manifest_text
        if filename == "conda.toml"
        else "dependencies: [zlib]\n"
    )
    parsed = ParsedInputFile.from_path(
        path, export_format="workspace-lock", lockfile_only=True
    )
    assert not parsed.is_lockfile
    assert parsed.exported_content is None
