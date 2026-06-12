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
    """Parsed conda input file with optional lockfile environments."""

    specs: list[str]
    channels: list[str]
    environment_format: EnvironmentFormat
    available_platforms: tuple[str, ...] = ()
    environments: tuple[Environment, ...] = ()

    @property
    def is_lockfile(self) -> bool:
        return self.environment_format == EnvironmentFormat.lockfile

    @classmethod
    def from_path(
        cls,
        path: str | os.PathLike[str],
        target_platforms: list[str] | tuple[str, ...] | None = None,
    ) -> ParsedInputFile:
        """Parse an input file through conda's plugin registry.

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
            raise ValueError(
                f"No conda environment spec plugin can handle: {path_str}"
            )

        environment_format = specifier.environment_format
        if environment_format == EnvironmentFormat.lockfile:
            available = tuple(getattr(spec, "available_platforms", ()) or ())
            targets = tuple(target_platforms or ())
            envs: tuple[Environment, ...] = ()
            if targets and available and set(targets).issubset(available):
                envs = tuple(spec.env_for(platform) for platform in targets)
            return cls(
                specs=[
                    str(spec)
                    for env in envs
                    for spec in env.requested_packages
                ],
                channels=list(
                    dict.fromkeys(
                        channel
                        for env in envs
                        if env.config and env.config.channels
                        for channel in env.config.channels
                    )
                ),
                environment_format=environment_format,
                available_platforms=available,
                environments=envs,
            )

        env = spec.env
        channels: list[str] = []
        if env.config and env.config.channels:
            channels.extend(env.config.channels)
        return cls(
            specs=[str(spec) for spec in env.requested_packages],
            channels=channels,
            environment_format=environment_format,
            environments=(env,),
        )

    @classmethod
    def from_content(
        cls,
        content: str,
        filename: str | None = None,
        target_platforms: list[str] | tuple[str, ...] | None = None,
    ) -> ParsedInputFile:
        """Parse in-memory input file content through conda's plugin registry."""
        filename = os.path.basename(filename or "environment.yml")
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported file extension '{ext}', "
                f"allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "@EXPLICIT":
                raise ValueError(
                    "Explicit package URL lockfiles are not accepted by the HTTP parser"
                )
            break

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / filename
            path.write_text(content)
            return cls.from_path(path, target_platforms)
