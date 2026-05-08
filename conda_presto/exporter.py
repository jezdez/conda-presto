"""Adapter over conda's exporter plugin registry.

Shared by both the CLI (``--format``) and the HTTP API
(``?format=``) so the two surfaces expose exactly the same set of
formats.  Any exporter registered by an installed plugin
(``explicit``, ``environment-yaml``, ``conda-lock-v1``,
``rattler-lock-v6``, …) is available from both.

The *default* CLI output (no ``--format``) and the *default* HTTP
output (no ``?format=``) are NOT produced here — they serialize
``list[SolveResult]`` directly via ``msgspec.json``.  That gives one
authoritative JSON shape for conda-presto's own output, with no
parallel implementation or conda plugin registration needed.
"""

from __future__ import annotations

import os

from conda.base.context import context
from conda.exceptions import CondaValueError
from conda.models.environment import Environment
from conda.plugins.types import CondaEnvironmentExporter

from .exceptions import UnknownFormatError

EXTENSION_MEDIA_TYPES: dict[str, str] = {
    ".yml": "application/yaml",
    ".yaml": "application/yaml",
    ".lock": "application/yaml",
    ".json": "application/json",
    ".toml": "application/toml",
    ".txt": "text/plain; charset=utf-8",
}

DEFAULT_MEDIA_TYPE = "text/plain; charset=utf-8"


def available_formats() -> list[str]:
    """Return the sorted list of registered exporter format names.

    Includes both primary names and aliases, so e.g. both
    ``rattler-lock-v6`` and ``pixi-lock-v6`` are listed when
    ``conda-lockfiles`` is installed.
    """
    return sorted(context.plugin_manager.get_exporter_format_mapping().keys())


def media_type_for(exporter: CondaEnvironmentExporter) -> str:
    """Pick a reasonable Content-Type for an exporter's output.

    Derived from the first recognized extension in the exporter's
    ``default_filenames`` — a conda plugin attribute — so new
    exporters are handled correctly without any per-format wiring
    here.  Unknown extensions fall back to UTF-8 plain text.

    Note that ``pixi.lock`` (extension ``.lock``) is YAML content,
    so ``.lock`` is mapped to ``application/yaml``.
    """
    for filename in exporter.default_filenames or ():
        ext = os.path.splitext(filename)[1].lower()
        if ext in EXTENSION_MEDIA_TYPES:
            return EXTENSION_MEDIA_TYPES[ext]
    return DEFAULT_MEDIA_TYPE


def render_envs(envs: list[Environment], format_name: str) -> tuple[str, str]:
    """Render *envs* via the named exporter plugin.

    Returns ``(body, media_type)``.  Raises :class:`UnknownFormatError`
    if the format is not registered or the exporter has neither
    ``multiplatform_export`` nor ``export`` set (the latter is a
    defensive check; conda itself rejects such plugins at registration
    time).
    """
    try:
        exporter = context.plugin_manager.get_environment_exporter_by_format(
            format_name
        )
    except CondaValueError as exc:
        raise UnknownFormatError(format_name, available_formats()) from exc

    if exporter.multiplatform_export:
        body = exporter.multiplatform_export(envs)
    elif exporter.export:
        body = "\n".join(exporter.export(env) for env in envs)
    else:
        raise UnknownFormatError(format_name, available_formats())

    return body, media_type_for(exporter)
