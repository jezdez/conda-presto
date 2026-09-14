"""Shared declaration exports use parsed environments without solving."""

from __future__ import annotations

import json
import time

import pytest
from conda.auxlib.exceptions import ValidationError as RecordValidationError
from conda.common.serialize.yaml import dumps as yaml_dumps
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


def test_locked_document_collection_is_complete_before_parser_returns(
    workspace_lock_path, no_declaration_solve
):
    parsed = ParsedInputFile.from_path(
        workspace_lock_path,
        target_platforms=["cpu", "gpu"],
        target_environments=["default", "test"],
        export_format="cyclonedx",
        export_each=True,
    )
    assert parsed.exported_content is None
    assert len(parsed.exported_documents) == 4
    assert all(
        json.loads(document.content)["components"]
        for document in parsed.exported_documents
    )


@pytest.mark.parametrize(
    "options,message",
    [
        ({"export_each": True}, "requires an output format"),
        ({"manifest_content": "[workspace]"}, "filename is required"),
        ({"manifest_filename": "conda.toml"}, "content is required"),
        (
            {"manifest_content": "[workspace]", "manifest_filename": "conda.toml"},
            "requires an output format",
        ),
    ],
)
def test_locked_export_rejects_incomplete_options(
    workspace_lock_path, options, message
):
    with pytest.raises(ValueError, match=message):
        ParsedInputFile.from_path(workspace_lock_path, **options)


def test_per_target_export_requires_workspace_lock(environment_yml_path):
    with pytest.raises(ValueError, match="require conda.lock"):
        ParsedInputFile.from_path(
            environment_yml_path, export_format="cyclonedx", export_each=True
        )


@pytest.mark.parametrize(
    "filename,content,message",
    [
        ("manifest.txt", "[workspace]", "Companion manifest filename"),
        ("conda\n.toml", "[workspace]", "unsupported characters"),
        ("conda.toml", "items = [" + "0," * 10_001 + "]", "complexity limit"),
        (
            "conda.toml",
            '[workspace]\nchannels = ["https://user:secret@example.test/channel"]\n',
            "credentials",
        ),
    ],
)
def test_companion_manifest_uses_bounded_parser_rules(
    workspace_lock_text, filename, content, message
):
    with pytest.raises(ValueError, match=message):
        ParsedInputFile.from_content_until(
            workspace_lock_text,
            "conda.lock",
            ["cpu"],
            time.monotonic() + 15,
            export_format="cyclonedx",
            target_environments=["test"],
            export_each=True,
            manifest_content=content,
            manifest_filename=filename,
        )


@pytest.mark.parametrize(
    "options,message",
    [
        ({"manifest_content": None}, "requires a companion manifest"),
        ({"target_platforms": ["cpu"]}, "without selectors"),
        ({"target_environments": ["test"]}, "without selectors"),
        ({"export_format": "explicit"}, "without selectors"),
    ],
)
def test_lock_check_requires_complete_manifest_without_overrides(
    workspace_consistent_lock_path, workspace_consistent_manifest_text, options, message
):
    arguments = {
        "check_lock": True,
        "manifest_content": workspace_consistent_manifest_text,
        "manifest_filename": "conda.toml",
    }
    arguments.update(options)
    with pytest.raises(ValueError, match=message):
        ParsedInputFile.from_path(workspace_consistent_lock_path, **arguments)


@pytest.mark.parametrize("filename", ["conda.toml", "pixi.toml", "pyproject.toml"])
def test_lock_check_uses_each_registered_workspace_manifest(
    tmp_path, workspace_consistent_lock_data, filename, no_declaration_solve
):
    data = workspace_consistent_lock_data
    for env in data["environments"].values():
        env["packages"] = {"linux-64": env["packages"]["cpu"]}
    path = tmp_path / "conda.lock"
    path.write_text(yaml_dumps(data))
    manifest = """\
[workspace]
name = "example"
channels = ["conda-forge"]
platforms = ["linux-64"]
[dependencies]
probe = "==1.0"
[environments]
test = []
"""
    if filename == "pyproject.toml":
        manifest = (
            manifest.replace("[", "[tool.conda.", 1)
            .replace("[dependencies]", "[tool.conda.dependencies]")
            .replace("[environments]", "[tool.conda.environments]")
        )
    parsed = ParsedInputFile.from_path(
        path, check_lock=True, manifest_content=manifest, manifest_filename=filename
    )
    assert parsed.lock_check.consistent
    assert len(parsed.lock_check.targets) == 2


@pytest.mark.parametrize(
    "invalid",
    [
        "version",
        "external-ref",
        "empty-bad-packages",
        "bad-dependency",
        "bad-match-spec",
    ],
)
def test_lock_check_rejects_malformed_or_unsupported_saved_records(
    workspace_consistent_lock_path,
    workspace_consistent_lock_data,
    workspace_consistent_manifest_text,
    invalid,
):
    data = workspace_consistent_lock_data
    if invalid == "version":
        data["version"] = 99
    elif invalid == "external-ref":
        data["environments"]["test"]["packages"]["gpu"] = [
            {"pypi": "https://example.test/package.whl"}
        ]
    elif invalid == "empty-bad-packages":
        data["environments"] = {}
        data["packages"] = "invalid"
    elif invalid == "bad-dependency":
        data["packages"][0]["depends"] = "invalid"
    else:
        data["packages"][0]["depends"] = [">=1"]
    workspace_consistent_lock_path.write_text(yaml_dumps(data))
    with pytest.raises((CondaError, ValueError, RecordValidationError)):
        ParsedInputFile.from_path(
            workspace_consistent_lock_path,
            check_lock=True,
            manifest_content=workspace_consistent_manifest_text,
            manifest_filename="conda.toml",
        )
