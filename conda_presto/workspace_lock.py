"""Workspace lock inspection and exact-record exports through upstream APIs."""

from __future__ import annotations

import hashlib
import io
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import msgspec
from conda.base.context import context
from conda.common.serialize.yaml import dump as yaml_dump
from conda.models.channel import Channel
from conda.models.environment import Environment
from conda.models.match_spec import MatchSpec
from conda_workspaces.context import WorkspaceContext
from conda_workspaces.lockfile import (
    FORMAT,
    CondaLockLoader,
    check_lockfile_satisfiability,
    load_lockfile_data,
    render_lockfile,
)
from conda_workspaces.manifests import PARSER_BY_FILENAME
from conda_workspaces.models import LockfileStatus, has_url_credentials_in_data
from conda_workspaces.resolver import resolve_environment

from .config import MAX_CHANNELS, MAX_PLATFORMS, MAX_SPECS
from .exceptions import WorkspaceSolveError, redact_safe_error, safe_error_message
from .exporter import OutputFormat
from .resolve import platform_lock
from .workspace import WorkspaceInput, WorkspaceTarget


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


class WorkspaceLockTargetCheck(msgspec.Struct):
    """Provider consistency result for one declared environment and target."""

    environment: str
    platform: str
    subdir: str
    consistent: bool
    reason: str | None


class WorkspaceLockCheckResult(msgspec.Struct):
    """Whether every declared environment and target satisfies the manifest."""

    consistent: bool
    targets: list[WorkspaceLockTargetCheck]


@dataclass(frozen=True)
class WorkspaceLockInput:
    """Adapt Workspaces lock selection to Presto requests and exporters."""

    loader: CondaLockLoader
    result: WorkspaceLockParseResult
    source_digest: str
    channels: list[str]
    source_data: dict[str, Any]
    manifest: WorkspaceInput | None = None
    manifest_digest: str | None = None
    manifest_content: str | None = None

    @classmethod
    def from_path(
        cls,
        path: Path,
        *,
        environments: list[str] | tuple[str, ...] | None = None,
        platforms: list[str] | tuple[str, ...] | None = None,
        select_all: bool = False,
        allow_empty: bool = False,
    ) -> WorkspaceLockInput:
        """Read lock data with Workspaces and select request-owned targets."""
        content = path.read_bytes()
        data = load_lockfile_data(content)
        if has_url_credentials_in_data(data):
            raise ValueError("Workspace lock input cannot contain URL credentials")
        data = CondaLockLoader.redact_data_urls(data)
        loader = CondaLockLoader(path, data=data)
        available = loader.available_environments
        names = list(dict.fromkeys(available if environments is None else environments))
        if (not names and not allow_empty) or platforms == [] or platforms == ():
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
        if allow_empty:
            # Missing saved targets are consistency mismatches, so validate
            # the records that exist without requiring a nonempty selection.
            nonempty = {
                name: targets for name, targets in selections.items() if targets
            }
            if nonempty:
                loader.select(nonempty)
            else:
                loader.package_records_by_url()
            selected_data = data
        else:
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
            data,
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
            manifest_content=content,
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

    def check_consistency(self) -> WorkspaceLockCheckResult:
        """Check every declared pair with target virtuals and upstream matching."""
        if self.manifest is None:
            raise ValueError("Lock consistency requires a companion manifest")
        targets = self.manifest.select().result.selected
        if not targets:
            raise ValueError("Declare at least one workspace environment and target")
        if any(target.pypi_dependencies for target in targets):
            raise ValueError("Lock consistency does not support PyPI dependencies")
        if any(
            name.removeprefix("__") == "archspec"
            for target in targets
            for name in target.system_requirements
        ):
            raise ValueError(
                "Lock consistency does not support archspec system requirements"
            )
        records = (
            record
            for environment in self.result.environments
            for platform in environment.platforms
            for record in self.loader.package_records_for_env_data(
                self.source_data, environment.name, platform
            )
        )
        # Invalid dependency syntax is an input error, rather than an unmet
        # requirement in the provider's consistency report.
        for record in records:
            for spec in (*record.depends, *record.constrains):
                MatchSpec(spec)
        checks = []
        for target in targets:
            resolved = resolve_environment(
                self.manifest.config, target.environment, target.platform
            )
            with target.virtual_package_context(resolved):
                status = check_lockfile_satisfiability(
                    self.manifest.config,
                    self.source_data,
                    target.platform,
                    environment=target.environment,
                )
            checks.append(
                WorkspaceLockTargetCheck(
                    target.environment,
                    target.platform,
                    target.subdir,
                    status.status == LockfileStatus.UP_TO_DATE,
                    redact_safe_error(status.reason) if status.reason else None,
                )
            )
        return WorkspaceLockCheckResult(
            all(check.consistent for check in checks), checks
        )

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

    def prepare_update(
        self, environment: str, platform: str, packages: tuple[str, ...]
    ) -> WorkspaceLockUpdate:
        """Require a complete baseline and explicitly declared update roots."""
        check = self.check_consistency()
        if not check.consistent:
            mismatch = next(target for target in check.targets if not target.consistent)
            raise ValueError(
                f"Baseline lock is inconsistent for {mismatch.environment!r} "
                f"on {mismatch.platform!r}: {mismatch.reason}"
            )
        if not environment or not platform or not packages:
            raise ValueError("Lock updates require an environment, target and packages")
        if len(packages) > MAX_SPECS:
            raise ValueError(f"Too many update packages: limit {MAX_SPECS}")
        workspace = self.manifest.select([environment], [platform])
        target = workspace.result.selected[0]
        if target.platform != platform:
            raise ValueError(
                "Lock updates require an exact declared logical target name"
            )
        resolved = resolve_environment(
            self.manifest.config, environment, target.platform
        )
        names = set(packages)
        unknown = names - resolved.conda_dependencies.keys()
        if unknown:
            raise ValueError(
                "Update packages must be exact declared direct conda names: "
                + ", ".join(sorted(unknown))
            )
        installed = {
            record.name
            for record in self.loader.package_records_for_env_data(
                self.source_data, environment, target.platform
            )
        }
        if missing := names - installed:
            raise ValueError(
                "Baseline is missing update roots: " + ", ".join(sorted(missing))
            )
        return WorkspaceLockUpdate(self, target, tuple(sorted(names)))

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


@dataclass(frozen=True)
class WorkspaceLockUpdate:
    """One validated update with its original complete baseline."""

    lock: WorkspaceLockInput
    target: WorkspaceTarget
    packages: tuple[str, ...]
    settings: dict[str, Any] = field(default_factory=dict)

    def configured(self) -> WorkspaceLockUpdate:
        """Carry the caller's effective solve settings into an isolated worker."""
        with platform_lock:
            settings = {
                name: getattr(context, name)
                for name in (
                    "pinned_packages",
                    "_aggressive_update_packages",
                    "deps_modifier",
                    "ignore_pinned",
                    "auto_update_conda",
                    "offline",
                    "use_index_cache",
                    "repodata_use_shards",
                    "repodata_fns",
                    "repodata_use_zst",
                    "ssl_verify",
                    "no_lock",
                    "use_only_tar_bz2",
                    "channel_priority",
                    "local_repodata_ttl",
                )
            }
            settings["_pkgs_dirs"] = tuple(context.pkgs_dirs)
        return replace(self, settings=settings)

    @contextmanager
    def solver_context(self, workspace: WorkspaceInput):
        """Apply caller settings and the declared target's virtual packages."""
        resolved = resolve_environment(
            workspace.config, self.target.environment, self.target.platform
        )
        with platform_lock, ExitStack() as stack:
            for name, value in self.settings.items():
                stack.enter_context(context._override(name, value))
            stack.enter_context(self.target.solver_context())
            stack.enter_context(self.target.virtual_package_context(resolved))
            yield resolved

    def repodata_options(self) -> dict[str, bool]:
        return {
            "use_shards": bool(self.settings.get("repodata_use_shards", True)),
            "use_index_cache": bool(self.settings.get("use_index_cache", False)),
        }

    def cache_identity(self) -> dict[str, Any]:
        """Identify exact inputs, update roots, target and effective solve settings."""
        workspace = self.lock.manifest.select(
            [self.target.environment], [self.target.platform]
        )
        with self.solver_context(workspace):
            solve_identity = workspace.cache_identity()
        return {
            "operation": "update",
            "input": self.lock.cache_identity(),
            "packages": self.packages,
            "solve": solve_identity,
            "settings": {
                name: value.value if isinstance(value, Enum) else value
                for name, value in self.settings.items()
            },
        }

    def solve(self, format_name: str | None = FORMAT) -> tuple[str, str]:
        """Update through Workspaces and return only a complete consistent lock."""
        if format_name != FORMAT:
            raise ValueError("Lock updates return conda-workspaces-lock-v1")
        with TemporaryDirectory(prefix="conda-presto-update-") as directory:
            try:
                # Input parser paths no longer exist after its process returns.
                path = Path(directory) / "conda.lock"
                stream = io.StringIO()
                yaml_dump(self.lock.source_data, stream)
                path.write_text(stream.getvalue(), encoding="utf-8")
                manifest_filename = Path(self.lock.manifest.config.manifest_path).name
                lock = WorkspaceLockInput.from_path(path).with_manifest(
                    self.lock.manifest_content, manifest_filename
                )
                with self.solver_context(lock.manifest) as resolved:
                    # This target is already resolved. The complete pre/post checks
                    # use a separate virtual package context for every declared pair.
                    content = render_lockfile(
                        WorkspaceContext(lock.manifest.config),
                        {self.target.environment: resolved},
                        baseline_data=lock.source_data,
                        update_targets={
                            (self.target.environment, self.target.platform): set(
                                self.packages
                            )
                        },
                    )
                path.write_text(content, encoding="utf-8")
                updated = WorkspaceLockInput.from_path(path).with_manifest(
                    self.lock.manifest_content, manifest_filename
                )
                check = updated.check_consistency()
                if not check.consistent:
                    mismatch = next(
                        target for target in check.targets if not target.consistent
                    )
                    raise WorkspaceSolveError(
                        self.target.environment,
                        self.target.platform,
                        f"Updated lock is inconsistent for {mismatch.environment!r} "
                        f"on {mismatch.platform!r}: {mismatch.reason}",
                    )
                return content, OutputFormat.named(FORMAT).media_type
            except WorkspaceSolveError:
                raise
            except Exception as exc:
                error = safe_error_message(exc.__cause__ or exc)
                error = error.replace(directory, "[temporary-directory]")
                raise WorkspaceSolveError(
                    self.target.environment, self.target.platform, error
                ) from exc
