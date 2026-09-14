"""Workspace manifest discovery and explicit environment selection."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from conda.base.constants import KNOWN_SUBDIRS
from conda.exceptions import CondaError
from conda_workspaces.manifests import PARSER_BY_FILENAME
from conda_workspaces.manifests.toml import WorkspaceDependencyResolver
from conda_workspaces.models import redact_channel_name
from conda_workspaces.resolver import resolve_environment

from .config import MAX_CHANNELS, MAX_PLATFORMS, MAX_SPECS
from .exceptions import redact_safe_error

if TYPE_CHECKING:
    from conda_workspaces.models import WorkspaceConfig


class WorkspaceEnvironment(msgspec.Struct):
    """An available workspace environment and its declared targets."""

    name: str
    features: list[str]
    no_default_feature: bool
    platforms: dict[str, str]


class WorkspaceTarget(msgspec.Struct):
    """Composed requirements for one explicitly selected target."""

    environment: str
    platform: str
    subdir: str
    specs: list[str]
    channels: list[str]
    channel_priority: str | None
    system_requirements: dict[str, str]
    pypi_dependencies: dict[str, str | dict[str, Any]]


class WorkspaceParseResult(msgspec.Struct):
    """Public workspace discovery and selection response."""

    format: str
    environments: list[WorkspaceEnvironment]
    selected: list[WorkspaceTarget]


@dataclass(frozen=True)
class WorkspaceInput:
    """Retain the provider configuration alongside its public response."""

    config: WorkspaceConfig
    result: WorkspaceParseResult

    @classmethod
    def filenames(cls) -> tuple[str, ...]:
        """Return filenames registered by the workspace parser provider."""
        return tuple(PARSER_BY_FILENAME)

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        environments: list[str] | tuple[str, ...] | None = None,
        platforms: list[str] | tuple[str, ...] | None = None,
    ) -> WorkspaceInput:
        """Read one supplied manifest without workspace discovery or solving."""
        path = Path(path).absolute()
        if (environments is not None and not environments) or (
            platforms is not None and not platforms
        ):
            raise ValueError("Workspace selectors cannot be empty")
        parser = PARSER_BY_FILENAME.get(path.name)
        if parser is None:
            raise ValueError("Unsupported workspace manifest filename")
        try:
            content = parser.read_manifest_text(path)
            data = parser.parse_toml_text_with_redacted_errors(content, path).unwrap()
            parser.validate_no_url_credentials(data, path, content=content)
            source = data
            if parser.format_alias == "pyproject":
                tool = data.get("tool", {})
                if not isinstance(tool, dict):
                    raise ValueError("The pyproject tool configuration must be a table")
                source = {}
                for name in ("conda", "pixi"):
                    section = tool.get(name, {})
                    if not isinstance(section, dict):
                        raise ValueError(
                            f"The tool.{name} configuration must be a table"
                        )
                    if section.get("workspace"):
                        source = section
                        break
            cls.validate_requirement_tables(source, path)
            config = parser.parse_text(path, content)
            resolved = {
                name: resolve_environment(config, name) for name in config.environments
            }
            available = [
                WorkspaceEnvironment(
                    name=name,
                    features=list(environment.features),
                    no_default_feature=environment.no_default_feature,
                    platforms={
                        target: resolved[name].platform_subdir(target)
                        for target in resolved[name].platforms
                    },
                )
                for name, environment in config.environments.items()
            ]
            workspace = cls(
                config,
                WorkspaceParseResult(parser.exporter_format, available, []),
            )
            if environments is None and platforms is None:
                return workspace

            names = list(
                dict.fromkeys(
                    environments if environments is not None else config.environments
                )
            )
            plans: list[tuple[str, str, str]] = []
            for name in names:
                if name not in resolved:
                    raise ValueError(f"Unknown workspace environment {name!r}")
                environment = resolved[name]
                declared = list(dict.fromkeys(environment.platforms))
                if platforms is None and not declared:
                    raise ValueError(
                        f"Environment {name!r} declares no platforms."
                        " Select an explicit conda platform."
                    )
                targets: list[str] = []
                for requested in platforms if platforms is not None else declared:
                    target = requested
                    if declared and requested not in declared:
                        matches = [
                            item
                            for item in declared
                            if environment.platform_subdir(item) == requested
                        ]
                        if len(matches) > 1:
                            raise ValueError(
                                f"Platform {requested!r} is ambiguous for {name!r}."
                                " Select a declared target name."
                            )
                        if not matches:
                            raise ValueError(
                                f"Unknown workspace platform {requested!r}"
                                f" for environment {name!r}"
                            )
                        target = matches[0]
                    subdir = environment.platform_subdir(target)
                    if subdir not in KNOWN_SUBDIRS:
                        raise ValueError(f"Unknown conda platform {subdir!r}")
                    if target not in targets:
                        targets.append(target)
                        plans.append((name, target, subdir))
                        if len(plans) > MAX_PLATFORMS:
                            raise ValueError(
                                "Too many workspace targets:"
                                f" limit {MAX_PLATFORMS}"
                                " (CONDA_PRESTO_MAX_PLATFORMS)"
                            )

            workspace.result.selected.extend(
                workspace.target(name, target, subdir) for name, target, subdir in plans
            )
            return workspace
        except OSError as exc:
            raise ValueError("Could not read the supplied workspace manifest") from exc
        except (CondaError, ValueError) as exc:
            message = getattr(exc, "reason", str(exc))
            message = message.replace(str(path), path.name)
            message = message.replace(str(path.parent), "[temporary-directory]")
            raise ValueError(redact_safe_error(message)) from exc

    @staticmethod
    def validate_requirement_tables(source: dict[str, Any], path: Path) -> None:
        """Reject requirements that tolerant provider parsing would alter or omit."""
        validator = WorkspaceDependencyResolver(path=path)
        pending = [source, source.get("workspace", {})]
        while pending:
            table = pending.pop()
            if not isinstance(table, dict):
                continue
            dependencies = table.get("dependencies", {})
            if not isinstance(dependencies, dict):
                raise ValueError("Conda dependencies must be a table")
            for name, dependency in dependencies.items():
                validator.reject_source_fields(name, dependency, "Conda dependencies")
            pypi_dependencies = table.get("pypi-dependencies", {})
            if not isinstance(pypi_dependencies, dict):
                raise ValueError("PyPI dependencies must be a table")
            for name, dependency in pypi_dependencies.items():
                if not isinstance(dependency, (str, dict)):
                    raise ValueError(
                        f"PyPI dependency {name!r} must be a string or table"
                    )
            channels = table.get("channels", [])
            if not isinstance(channels, list) or any(
                not isinstance(channel, str)
                and not (
                    isinstance(channel, dict)
                    and isinstance(channel.get("channel"), str)
                )
                for channel in channels
            ):
                raise ValueError(
                    "Workspace channels must be a list of strings"
                    " or tables with a string channel field"
                )
            for key in ("feature", "environments", "target"):
                children = table.get(key, {})
                if isinstance(children, dict):
                    pending.extend(children.values())

    def target(self, name: str, platform: str, subdir: str) -> WorkspaceTarget:
        """Compose and validate one selected environment using the provider."""
        resolved = resolve_environment(self.config, name, platform)
        for dependency in resolved.conda_dependencies.values():
            if dependency.get_raw_value("url"):
                raise ValueError(
                    f"Environment {name!r} has an unsupported conda URL dependency:"
                    f" {dependency.name!r}"
                )
        for dependency in resolved.pypi_dependencies.values():
            if dependency.path or dependency.git or dependency.url:
                raise ValueError(
                    f"Environment {name!r} has an unsupported PyPI source dependency:"
                    f" {dependency.name!r}."
                    " Local paths, Git and URLs are not supported."
                )
        if resolved.pypi_dependencies and find_spec("conda_pypi") is None:
            raise ValueError(
                f"Environment {name!r} requires conda-pypi for its PyPI dependencies"
            )
        specs = [str(spec) for spec in resolved.requested_packages_for_export()]
        channels = [redact_channel_name(channel) for channel in resolved.channels]
        if len(specs) + len(resolved.pypi_dependencies) > MAX_SPECS:
            raise ValueError(
                f"Too many specs for {name!r}: limit {MAX_SPECS}"
                " (CONDA_PRESTO_MAX_SPECS)"
            )
        if len(channels) > MAX_CHANNELS:
            raise ValueError(
                f"Too many channels for {name!r}: limit {MAX_CHANNELS}"
                " (CONDA_PRESTO_MAX_CHANNELS)"
            )
        return WorkspaceTarget(
            environment=name,
            platform=platform,
            subdir=subdir,
            specs=specs,
            channels=channels,
            channel_priority=resolved.channel_priority,
            system_requirements=dict(resolved.system_requirements),
            pypi_dependencies={
                name: dependency.to_toml()
                for name, dependency in resolved.pypi_dependencies.items()
            },
        )
