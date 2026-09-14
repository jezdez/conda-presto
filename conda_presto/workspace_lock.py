"""Workspace lock inspection and exact-record exports through upstream APIs."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import msgspec
from conda.common.serialize.yaml import dump as yaml_dump
from conda.models.channel import Channel
from conda.models.environment import Environment
from conda_workspaces.lockfile import FORMAT, CondaLockLoader, load_lockfile_data
from conda_workspaces.manifests import PARSER_BY_FILENAME
from conda_workspaces.models import has_url_credentials_in_data
from conda_workspaces.resolver import resolve_environment

from .config import MAX_CHANNELS, MAX_PLATFORMS
from .exporter import OutputFormat
from .workspace import WorkspaceInput


class WorkspaceLockEnvironment(msgspec.Struct):
    """Targets saved for a named environment, with inferable conda subdirs."""

    name: str
    platforms: dict[str, str | None]


class WorkspaceLockTarget(msgspec.Struct):
    """A selected logical target and its concrete conda subdir, when known."""

    environment: str
    platform: str
    subdir: str | None


class WorkspaceLockParseResult(msgspec.Struct):
    """Workspace lock discovery and explicit selection."""

    format: str
    environments: list[WorkspaceLockEnvironment]
    selected: list[WorkspaceLockTarget]


class WorkspaceLockExport(msgspec.Struct):
    """One exported document identified by its saved environment and target."""

    environment: str
    platform: str
    subdir: str | None
    content: str


@dataclass(frozen=True)
class WorkspaceLockInput:
    """Adapt Workspaces lock selection to Presto requests and exporters."""

    loader: CondaLockLoader
    result: WorkspaceLockParseResult
    source_digest: str
    channels: list[str]
    manifest: WorkspaceInput | None = None
    manifest_digest: str | None = None

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        environments: list[str] | tuple[str, ...] | None = None,
        platforms: list[str] | tuple[str, ...] | None = None,
        select_all: bool = False,
    ) -> WorkspaceLockInput:
        """Read lock data with Workspaces and select request-owned targets."""
        content = path.read_bytes()
        data = load_lockfile_data(content)
        if has_url_credentials_in_data(data):
            raise ValueError("Workspace lock input cannot contain URL credentials")
        loader = CondaLockLoader(path, data=data)
        available = loader.available_environments
        names = list(dict.fromkeys(available if environments is None else environments))
        if not names or platforms == [] or platforms == ():
            raise ValueError("Select at least one lockfile environment and target")

        selections = {}
        for name in names:
            declared = loader.platforms_for(name)
            targets = []
            for requested in declared if platforms is None else platforms:
                target = requested
                if requested not in declared:
                    matches = [
                        item
                        for item in declared
                        if loader.package_platform_for(item, name) == requested
                    ]
                    if len(matches) > 1:
                        raise ValueError(
                            f"Platform {requested!r} is ambiguous for {name!r}. "
                            "Select a saved target name."
                        )
                    if not matches:
                        raise ValueError(
                            f"Unknown lockfile target {requested!r} "
                            f"for environment {name!r}"
                        )
                    target = matches[0]
                if target not in targets:
                    targets.append(target)
            selections[name] = targets
        if sum(map(len, selections.values())) > MAX_PLATFORMS:
            raise ValueError(
                f"Too many lockfile targets: limit {MAX_PLATFORMS} "
                "(CONDA_PRESTO_MAX_PLATFORMS)"
            )
        selected_data = loader.select(selections)
        channels = list(
            dict.fromkeys(
                entry["url"]
                for environment in selected_data["environments"].values()
                for entry in environment["channels"]
            )
        )
        if len(channels) > MAX_CHANNELS:
            raise ValueError(f"Too many lockfile channels: limit {MAX_CHANNELS}")
        discovery = [
            WorkspaceLockEnvironment(
                name,
                {
                    target: loader.package_platform_for(target, name)
                    for target in loader.platforms_for(name)
                },
            )
            for name in available
        ]
        selected = (
            [
                WorkspaceLockTarget(
                    name, target, loader.package_platform_for(target, name)
                )
                for name, targets in selections.items()
                for target in targets
            ]
            if select_all or environments is not None or platforms is not None
            else []
        )
        return cls(
            loader,
            WorkspaceLockParseResult(FORMAT, discovery, selected),
            hashlib.sha256(content).hexdigest(),
            channels,
        )

    def with_manifest(self, content: str, filename: str) -> WorkspaceLockInput:
        """Retain validated companion declarations for selected locked records."""
        parser = PARSER_BY_FILENAME.get(filename)
        if parser is None:
            raise ValueError(
                "Companion manifest filename must be conda.toml, pixi.toml "
                "or pyproject.toml"
            )
        config = parser.parse_text(
            self.loader.path.with_name(filename), content, reject_url_credentials=True
        )
        manifest = WorkspaceInput.from_config(config, parser.exporter_format)
        return replace(
            self,
            manifest=manifest,
            manifest_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    def environment(self, target: WorkspaceLockTarget) -> Environment:
        """Load exact records and optionally verify their declared direct roots."""
        subdir = target.subdir
        resolved = None
        if self.manifest is not None:
            selection = self.manifest.select([target.environment], [target.platform])
            declared = selection.result.selected[0]
            if subdir is not None and subdir != declared.subdir:
                raise ValueError("Companion manifest platform does not match the lock")
            subdir = declared.subdir
            resolved = resolve_environment(
                self.manifest.config, target.environment, declared.platform
            )
            if resolved.pypi_dependencies:
                raise ValueError(
                    "Companion manifest PyPI dependencies cannot be verified "
                    "from this conda lockfile"
                )
        if subdir is None:
            raise ValueError(
                "Cannot infer a concrete conda subdir for this logical target. "
                "Provide a matching companion manifest or use "
                "conda-workspaces-lock-v1 to extract its saved entries."
            )
        env = self.loader.env_for(
            target.platform,
            name=target.environment,
            package_platform=subdir,
            metadata_only=True,
        )
        # The lock records do not identify which dependencies a user requested.
        env.requested_packages = []
        if resolved is not None:
            if tuple(
                Channel(channel).canonical_name for channel in resolved.channels
            ) != (
                tuple(
                    Channel(channel).canonical_name for channel in env.config.channels
                )
            ):
                raise ValueError("Companion manifest channels do not match the lock")
            env.requested_packages = resolved.requested_packages_for_export(
                env.explicit_packages
            )
        return env

    def render(self, format_name: str) -> str:
        """Extract source entries or export metadata-only conda environments."""
        output = OutputFormat.named(format_name)
        targets = self.result.selected
        if not targets:
            raise ValueError("Select at least one lockfile environment and target")
        if output.exporter.name == FORMAT:
            if self.manifest is not None:
                raise ValueError(
                    "Companion manifests cannot modify source lockfile extraction"
                )
            selections: dict[str, list[str]] = {}
            for target in targets:
                selections.setdefault(target.environment, []).append(target.platform)
            data = self.loader.select(selections)
            stream = io.StringIO()
            yaml_dump(data, stream)
            return stream.getvalue()

        if output.exporter.name in {"conda-lock-v1", "rattler-lock-v6"}:
            raise ValueError(
                "The conda-lockfiles exporter cannot preserve workspace lock "
                "metadata. Use conda-workspaces-lock-v1 to extract saved entries."
            )

        if len({target.environment for target in targets}) != 1:
            raise ValueError(
                "Select one environment for this output format. "
                "Use conda-workspaces-lock-v1 for combined environments."
            )
        envs = [self.environment(target) for target in targets]
        if len({env.platform for env in envs}) != len(targets):
            raise ValueError(
                "This output format cannot represent targets sharing a conda subdir"
            )
        if not output.exporter.multiplatform_export and len(targets) != 1:
            raise ValueError("Select one target for this output format")
        return output.render(envs)[0]

    def render_each(self, format_name: str) -> list[WorkspaceLockExport]:
        """Render every selected pair completely before returning documents."""
        output = OutputFormat.named(format_name)
        if output.is_lockfile:
            raise ValueError("Per-target documents require an environment exporter")
        if not self.result.selected:
            raise ValueError("Select at least one lockfile environment and target")
        documents = []
        for target in self.result.selected:
            env = self.environment(target)
            documents.append(
                WorkspaceLockExport(
                    target.environment,
                    target.platform,
                    env.platform,
                    output.render([env])[0],
                )
            )
        return documents

    def cache_identity(self) -> dict[str, Any]:
        """Identify uploaded bytes, saved selections and their parser providers."""
        return {
            "source": self.source_digest,
            "selected": msgspec.to_builtins(self.result.selected),
            "manifest": (
                {"source": self.manifest_digest, "format": self.manifest.result.format}
                if self.manifest is not None
                else None
            ),
            "providers": {
                name: OutputFormat.provider_versions(name)
                for name in ("conda", "conda_workspaces", "conda_lockfiles")
            },
        }
