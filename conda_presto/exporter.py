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
from dataclasses import dataclass

from conda.base.context import context
from conda.exceptions import CondaValueError
from conda.models.environment import Environment
from conda.plugins.types import CondaEnvironmentExporter, EnvironmentFormat

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


@dataclass(frozen=True)
class OutputFormat:
    """Named conda exporter plugin used for non-default output formats."""

    name: str
    exporter: CondaEnvironmentExporter

    @classmethod
    def available(cls) -> list[str]:
        """Return the sorted list of registered exporter format names.

        Includes both primary names and aliases, so e.g. both
        ``rattler-lock-v6`` and ``pixi-lock-v6`` are listed when
        ``conda-lockfiles`` is installed.
        """
        return sorted(context.plugin_manager.get_exporter_format_mapping().keys())

    @classmethod
    def named(cls, name: str) -> OutputFormat:
        """Return the named output format or raise ``UnknownFormatError``."""
        try:
            exporter = context.plugin_manager.get_environment_exporter_by_format(name)
        except CondaValueError as exc:
            raise UnknownFormatError(name, cls.available()) from exc
        return cls(name=name, exporter=exporter)

    @property
    def is_lockfile(self) -> bool:
        return self.exporter.environment_format == EnvironmentFormat.lockfile

    @property
    def media_type(self) -> str:
        """Pick a reasonable Content-Type for this format's output.

        Derived from the first recognized extension in the exporter's
        ``default_filenames`` — a conda plugin attribute — so new
        exporters are handled correctly without any per-format wiring
        here.  Unknown extensions fall back to UTF-8 plain text.

        Note that ``pixi.lock`` (extension ``.lock``) is YAML content,
        so ``.lock`` is mapped to ``application/yaml``.
        """
        for filename in self.exporter.default_filenames or ():
            ext = os.path.splitext(filename)[1].lower()
            if ext in EXTENSION_MEDIA_TYPES:
                return EXTENSION_MEDIA_TYPES[ext]
        return DEFAULT_MEDIA_TYPE

    def render(self, envs: list[Environment]) -> tuple[str, str]:
        """Render *envs* via this exporter plugin.

        Returns ``(body, media_type)``.  Raises :class:`UnknownFormatError`
        if the exporter has neither ``multiplatform_export`` nor
        ``export`` set. The latter is a defensive check; conda itself
        rejects such plugins at registration time.
        """
        if self.exporter.multiplatform_export:
            body = self.exporter.multiplatform_export(envs)
        elif self.exporter.export:
            body = "\n".join(self.exporter.export(env) for env in envs)
        else:
            raise UnknownFormatError(self.name, self.available())

        return body, self.media_type
