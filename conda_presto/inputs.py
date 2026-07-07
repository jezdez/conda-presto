"""Input-file parsing helpers shared by the CLI and HTTP API."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from conda.base.context import context
from conda.models.environment import Environment
from conda.plugins.types import EnvironmentFormat

ALLOWED_EXTENSIONS = {".yml", ".yaml", ".txt", ".lock", ".toml", ".json"}


@dataclass(frozen=True)
class ParsedInputFile:
    """Parsed conda environment input with optional lockfile envs."""

    specs: list[str]
    channels: list[str]
    environment_format: EnvironmentFormat
    available_platforms: tuple[str, ...] = ()
    environments: tuple[Environment, ...] = ()

    @property
    def is_lockfile(self) -> bool:
        return self.environment_format == EnvironmentFormat.lockfile


def _channels_from_envs(envs: tuple[Environment, ...]) -> list[str]:
    return list(
        dict.fromkeys(
            channel
            for env in envs
            if env.config and env.config.channels
            for channel in env.config.channels
        )
    )


def _specs_from_envs(envs: tuple[Environment, ...]) -> list[str]:
    return [str(spec) for env in envs for spec in env.requested_packages]


def parse_environment_path(
    path: str | os.PathLike[str],
    target_platforms: list[str] | tuple[str, ...] | None = None,
) -> ParsedInputFile:
    """Parse an environment file through conda's plugin registry.

    ``target_platforms`` is only used for lockfiles. When every target
    platform is present, ``environments`` contains the corresponding
    parsed ``Environment`` objects. When a target is missing, parsing
    still succeeds but ``environments`` is empty so callers can decide
    whether to fall back to solving or fail a no-solve request.
    """
    path_str = str(path)
    specifier = context.plugin_manager.detect_environment_specifier(path_str)
    spec = specifier.environment_spec(path_str)
    if not spec.can_handle():
        raise ValueError(f"No conda environment spec plugin can handle: {path_str}")

    if specifier.environment_format == EnvironmentFormat.lockfile:
        available = tuple(getattr(spec, "available_platforms", ()) or ())
        targets = tuple(target_platforms or ())
        envs: tuple[Environment, ...] = ()
        if targets and available and set(targets).issubset(available):
            envs = tuple(spec.env_for(platform) for platform in targets)
        return ParsedInputFile(
            specs=_specs_from_envs(envs),
            channels=_channels_from_envs(envs),
            environment_format=specifier.environment_format,
            available_platforms=available,
            environments=envs,
        )

    env = spec.env
    channels: list[str] = []
    if env.config and env.config.channels:
        channels.extend(env.config.channels)
    return ParsedInputFile(
        specs=[str(spec) for spec in env.requested_packages],
        channels=channels,
        environment_format=specifier.environment_format,
        environments=(env,),
    )


def parse_environment_content(
    content: str,
    filename: str | None = None,
    target_platforms: list[str] | tuple[str, ...] | None = None,
) -> ParsedInputFile:
    """Parse in-memory file content through conda's plugin registry."""
    filename = os.path.basename(filename or "environment.yml")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext}', "
            f"allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / filename
        path.write_text(content)
        return parse_environment_path(path, target_platforms)
