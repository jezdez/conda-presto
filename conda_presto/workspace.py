"""Workspace manifest discovery and explicit environment selection."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

import msgspec
from conda.base.constants import KNOWN_SUBDIRS
from conda.base.context import context
from conda.core.index import Index
from conda.exceptions import CondaError
from conda.models.channel import Channel
from conda.models.environment import Environment, EnvironmentConfig
from conda.models.version import VersionOrder
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.manifests import PARSER_BY_FILENAME
from conda_workspaces.models import redact_channel_name
from conda_workspaces.resolver import resolve_environment
from packaging.requirements import Requirement

from .config import MAX_CHANNELS, MAX_PLATFORMS, MAX_SPECS
from .exceptions import (
    CredentialRedactionFilter,
    WorkspaceSolveError,
    redact_safe_error,
    safe_error_message,
)
from .exporter import OutputFormat
from .resolve import VIRTUAL_PACKAGES, ResolvedPackage, platform_lock

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

    @contextmanager
    def solver_context(self):
        """Scope target virtual packages without inheriting a previous solve."""
        overrides = dict(VIRTUAL_PACKAGES.get(self.subdir.split("-", 1)[0], {}))
        overrides.update(
            (name.removeprefix("__"), value)
            for name, value in self.system_requirements.items()
        )
        with (
            platform_lock,
            context._override("_subdir", self.subdir),
            context._override("_override_virtual_packages", overrides),
            context._override("solver", "rattler"),
            context._override("json", True),
        ):
            yield


class WorkspaceSolveResult(msgspec.Struct):
    """A native solve result identified by environment and logical target."""

    environment: str
    platform: str
    subdir: str
    packages: list[ResolvedPackage]
    error: str | None = None


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
        parser = PARSER_BY_FILENAME.get(path.name)
        if parser is None:
            raise ValueError("Unsupported workspace manifest filename")
        try:
            content = parser.read_manifest_text(path)
            config = parser.parse_text(path, content, reject_url_credentials=True)
            return cls.from_config(
                config,
                parser.exporter_format,
                environments=environments,
                platforms=platforms,
            )
        except OSError as exc:
            raise ValueError("Could not read the supplied workspace manifest") from exc
        except (CondaError, ValueError) as exc:
            message = getattr(exc, "reason", str(exc))
            message = message.replace(str(path), path.name)
            message = message.replace(str(path.parent), "[temporary-directory]")
            raise ValueError(redact_safe_error(message)) from exc

    @classmethod
    def from_config(
        cls,
        config: WorkspaceConfig,
        format_name: str,
        *,
        environments: list[str] | tuple[str, ...] | None = None,
        platforms: list[str] | tuple[str, ...] | None = None,
    ) -> WorkspaceInput:
        """Discover or select requirements from an already parsed manifest."""
        if (
            environments is not None
            and not environments
            or platforms is not None
            and not platforms
        ):
            raise ValueError("Workspace selectors cannot be empty")
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
            WorkspaceParseResult(format_name, available, []),
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

    def select(
        self,
        environments: list[str] | None = None,
        platforms: list[str] | None = None,
    ) -> WorkspaceInput:
        """Select declared environments and targets, defaulting to all of them."""
        return self.from_config(
            self.config,
            self.result.format,
            environments=list(self.config.environments)
            if environments is None
            else environments,
            platforms=platforms,
        )

    def validate_output(self, format_name: str | None) -> None:
        """Reject selected combinations that an exporter cannot represent."""
        if format_name is None:
            return
        output = OutputFormat.named(format_name)
        combined = output.exporter.name == "conda-workspaces-lock-v1"
        targets = self.result.selected
        if not combined:
            if len({target.environment for target in targets}) != 1:
                raise ValueError(
                    "Select one environment for this output format. "
                    "Use conda-workspaces-lock-v1 for combined environments."
                )
            if len({target.subdir for target in targets}) != len(targets):
                raise ValueError(
                    "This output format cannot represent targets sharing a conda subdir"
                )
            if not output.exporter.multiplatform_export and len(targets) != 1:
                raise ValueError("Select one target for this output format")
        channels_by_environment = {}
        for target in targets:
            channels = channels_by_environment.setdefault(
                target.environment, target.channels
            )
            if channels != target.channels:
                raise ValueError(
                    "This output format requires the same ordered channels "
                    "for every target of an environment"
                )

    def solve_channels(
        self, target: WorkspaceTarget, packages: tuple[str, ...] | None = None
    ) -> list[str]:
        """Include channels requested by dependencies without changing declarations."""
        resolved = resolve_environment(self.config, target.environment, target.platform)
        channels = list(resolved.channels)
        specs = (
            list(resolved.conda_dependencies.values())
            if packages is None
            else [resolved.conda_dependencies[name] for name in sorted(packages)]
        )
        specs.extend(resolved.system_requirement_specs())
        for dependency in specs:
            if channel := dependency.get_exact_value("channel"):
                # Match rattler's recovery of file channels from original specs.
                original = dependency.original_spec_str
                if original and original.startswith("file://"):
                    channel = Channel(original.split("::")[0])
                channels.append(channel)
        return list(
            dict.fromkeys(
                redact_channel_name(url)
                for channel in channels
                for url in channel.base_urls
            )
        )

    def cache_identity(self) -> dict[str, Any]:
        """Describe selected requirements, virtual packages and their providers."""
        targets = []
        for target in self.result.selected:
            with target.solver_context():
                virtual_packages = sorted(
                    json.dumps(record.dump(), sort_keys=True, default=str)
                    for record in Index().system_packages
                )
            targets.append(
                (
                    msgspec.to_builtins(target),
                    virtual_packages,
                    self.solve_channels(target),
                )
            )
        providers = {}
        for name in ("conda-workspaces", "conda-lockfiles", "conda-pypi"):
            try:
                providers[name] = version(name)
            except PackageNotFoundError:
                providers[name] = None
        return {
            "targets": targets,
            "providers": providers,
            "repodata": self.repodata_options(),
        }

    def export(self, format_name: str) -> tuple[str, str]:
        """Render selected declarations through the Workspaces and conda APIs."""
        workspace = self if self.result.selected else self.select()
        output = OutputFormat.named(format_name)
        output.validate_declared_input(len(workspace.result.selected))
        workspace.validate_output(format_name)
        environments = []
        for target in workspace.result.selected:
            with target.solver_context():
                environments.extend(
                    WorkspaceContext(workspace.config).envs_from_manifest(
                        target.environment, requested_platforms=(target.platform,)
                    )
                )
        return output.render(environments)

    @staticmethod
    def repodata_options() -> dict[str, bool]:
        """Match the metadata sources used by the public rattler solver."""
        return {
            "use_shards": bool(getattr(context, "repodata_use_shards", True)),
            "use_index_cache": bool(context.use_index_cache),
        }

    def solve(
        self, format_name: str | None = None
    ) -> list[WorkspaceSolveResult] | tuple[str, str]:
        """Solve selected targets with Workspaces and render complete records."""
        CredentialRedactionFilter.install()
        workspace = self if self.result.selected else self.select()
        workspace.validate_output(format_name)
        output = OutputFormat.named(format_name) if format_name is not None else None
        results = []
        environments = []
        with TemporaryDirectory(prefix="conda-presto-solve-") as directory:
            for index, target in enumerate(workspace.result.selected):
                try:
                    with target.solver_context():
                        resolved = resolve_environment(
                            workspace.config, target.environment, target.platform
                        )
                        records = resolved.solve_for_platform(
                            target.subdir, prefix=Path(directory) / str(index)
                        )
                        if output is not None:
                            if output.exporter.name == "conda-workspaces-lock-v1":
                                environment = Environment(
                                    name=target.environment,
                                    platform=target.subdir,
                                    config=EnvironmentConfig(
                                        channels=tuple(target.channels)
                                    ),
                                    explicit_packages=records,
                                )
                                environment.lock_platform = target.platform
                            else:
                                environment = WorkspaceContext(
                                    workspace.config
                                ).envs_from_manifest(
                                    target.environment,
                                    requested_platforms=(target.platform,),
                                )[0]
                                environment.explicit_packages = records
                            environments.append(environment)
                except Exception as exc:
                    error = safe_error_message(exc.__cause__ or exc)
                    if output is not None:
                        raise WorkspaceSolveError(
                            target.environment, target.platform, error
                        ) from exc
                    results.append(
                        WorkspaceSolveResult(
                            target.environment,
                            target.platform,
                            target.subdir,
                            [],
                            error,
                        )
                    )
                else:
                    results.append(
                        WorkspaceSolveResult(
                            target.environment,
                            target.platform,
                            target.subdir,
                            [ResolvedPackage.from_record(record) for record in records],
                        )
                    )
        return output.render(environments) if output is not None else results

    def target(self, name: str, platform: str, subdir: str) -> WorkspaceTarget:
        """Compose and validate one selected environment using the provider."""
        resolved = resolve_environment(self.config, name, platform)
        for dependency in (
            *resolved.conda_dependencies.values(),
            *resolved.system_requirement_specs(),
        ):
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
            try:
                extras = f"[{','.join(dependency.extras)}]" if dependency.extras else ""
                version_spec = "" if dependency.spec == "*" else dependency.spec or ""
                requirement = Requirement(f"{dependency.name}{extras}{version_spec}")
                if (
                    requirement.name != dependency.name
                    or requirement.extras != set(dependency.extras)
                    or requirement.url
                    or requirement.marker
                ):
                    raise ValueError("Expected a PyPI name, extras and version")
                for specifier in requirement.specifier:
                    if specifier.operator == "===":
                        # Arbitrary PyPI versions must not become MatchSpec selectors.
                        VersionOrder(specifier.version)
            except (ValueError, CondaError) as exc:
                raise ValueError(
                    f"Environment {name!r} has an invalid PyPI requirement:"
                    f" {dependency.name!r}"
                ) from exc
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
