"""Tests for conda_presto.exporter (render_envs adapter only).

The native JSON output produced by the CLI and HTTP API is covered by
``tests/test_resolve.py`` and ``tests/test_app.py``; those paths do
not go through this module.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conda.base.context import context
from conda.models.environment import Environment

from conda_presto.exceptions import UnknownFormatError
from conda_presto.exporter import (
    available_formats,
    media_type_for,
    render_envs,
)


@pytest.fixture()
def env_with_records(make_package_record):
    records = [
        make_package_record(name="zlib", version="1.3.1"),
        make_package_record(name="bzip2", version="1.0.8", build="h0"),
    ]
    return Environment(platform="linux-64", explicit_packages=records)


@pytest.mark.parametrize(
    "format_name, expected",
    [
        pytest.param("environment-json", "application/json", id="environment-json"),
        pytest.param("json", "application/json", id="json-alias"),
        pytest.param("environment-yaml", "application/yaml", id="yaml"),
        pytest.param("yaml", "application/yaml", id="yaml-alias"),
        pytest.param("conda-lock-v1", "application/yaml", id="conda-lock-v1"),
        pytest.param("pixi-lock-v6", "application/yaml", id="pixi-lock-v6"),
        pytest.param("explicit", "text/plain; charset=utf-8", id="explicit"),
        pytest.param("requirements", "text/plain; charset=utf-8", id="requirements"),
    ],
)
def test_media_type_for(format_name, expected):
    exporter = context.plugin_manager.get_environment_exporter_by_format(format_name)
    assert media_type_for(exporter) == expected


def test_media_type_for_unknown_extension_falls_back_to_text():
    """Extensions not in the lookup table get plain text."""
    fake = SimpleNamespace(default_filenames=("output.xyz",))
    assert media_type_for(fake) == "text/plain; charset=utf-8"


def test_media_type_for_no_default_filenames():
    """Exporters without default_filenames get plain text."""
    fake = SimpleNamespace(default_filenames=())
    assert media_type_for(fake) == "text/plain; charset=utf-8"


def test_available_formats_lists_builtins():
    formats = available_formats()
    assert "explicit" in formats
    assert "environment-yaml" in formats
    assert formats == sorted(formats)


def test_available_formats_includes_conda_lockfiles():
    """conda-lockfiles is a base dependency; its formats must be available."""
    formats = available_formats()
    assert "conda-lock-v1" in formats
    assert "rattler-lock-v6" in formats
    assert "pixi-lock-v6" in formats


def test_render_envs_unknown_format_raises(env_with_records):
    with pytest.raises(UnknownFormatError) as excinfo:
        render_envs([env_with_records], "nope-not-a-format")
    assert excinfo.value.format_name == "nope-not-a-format"
    assert "explicit" in excinfo.value.available
    assert "nope-not-a-format" in str(excinfo.value)


def test_render_envs_explicit(env_with_records):
    body, media_type = render_envs([env_with_records], "explicit")
    assert media_type.startswith("text/plain")
    assert "@EXPLICIT" in body


def test_render_envs_environment_yaml(env_with_records):
    body, media_type = render_envs([env_with_records], "environment-yaml")
    assert media_type == "application/yaml"
    assert "dependencies:" in body


@pytest.mark.parametrize(
    "exporter_attrs, envs, expected",
    [
        pytest.param(
            {"multiplatform_export": lambda e: f"multi:{len(e)}"},
            ["env1", "env2"],
            "multi:2",
            id="multiplatform-many",
        ),
        pytest.param(
            {"multiplatform_export": lambda e: f"multi:{len(e)}"},
            ["env1"],
            "multi:1",
            id="multiplatform-single",
        ),
        pytest.param(
            {"export": lambda env: f"single:{env}"},
            ["env1", "env2"],
            "single:env1\nsingle:env2",
            id="single-export-fallback",
        ),
    ],
)
def test_render_envs_dispatches_to_exporter_methods(
    monkeypatch, exporter_attrs, envs, expected
):
    """render_envs prefers multiplatform_export, falls back to export."""
    attrs = {
        "multiplatform_export": None,
        "export": None,
        "default_filenames": ("out.txt",),
        **exporter_attrs,
    }
    monkeypatch.setattr(
        "conda_presto.exporter.context.plugin_manager"
        ".get_environment_exporter_by_format",
        lambda fmt: SimpleNamespace(**attrs),
    )
    body, _ = render_envs(envs, "test-fmt")
    assert body == expected


def test_render_envs_no_export_method_raises(monkeypatch):
    """An exporter with neither method raises UnknownFormatError."""
    fake = SimpleNamespace(
        multiplatform_export=None,
        export=None,
        default_filenames=("out.txt",),
    )
    monkeypatch.setattr(
        "conda_presto.exporter.context.plugin_manager"
        ".get_environment_exporter_by_format",
        lambda fmt: fake,
    )
    with pytest.raises(UnknownFormatError) as excinfo:
        render_envs(["env1"], "test-fmt")
    assert excinfo.value.format_name == "test-fmt"
