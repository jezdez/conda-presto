"""No-fetch lockfile transcoding for released conda-lockfiles versions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from conda.base.context import context
from conda.exceptions import CondaValueError
from conda.models.channel import Channel
from conda.models.environment import Environment, EnvironmentConfig
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord
from conda.plugins.types import EnvironmentSpecBase

from .exporter import OutputFormat

SUPPORTED_FORMATS = frozenset({"conda-lock-v1", "rattler-lock-v6"})


@dataclass(frozen=True)
class CondaLockfilesTranscoder:
    """Render conda-lockfiles formats without fetching package archives."""

    specifier: EnvironmentSpecBase

    def render(
        self,
        platforms: Iterable[str],
        *,
        format_name: str,
    ) -> str | None:
        """Render supported source and target formats through conda exporters."""
        output_format = OutputFormat.named(format_name)
        target_format = output_format.exporter.name
        if target_format not in SUPPORTED_FORMATS:
            return None

        requested = tuple(dict.fromkeys(platforms))
        if not requested:
            raise CondaValueError("At least one platform is required for transcoding.")

        # This is intentionally a concrete compatibility path, not a second
        # plugin API. Delete it after conda/conda-lockfiles#161 is released and
        # conda-presto's minimum conda-lockfiles version includes transcode().
        from conda_lockfiles import CONDA_PYPI_CHANNEL_NAME
        from conda_lockfiles.conda_lock import v1 as conda_lock_v1
        from conda_lockfiles.load_yaml import load_yaml
        from conda_lockfiles.rattler_lock import v6 as rattler_lock_v6

        if isinstance(self.specifier, conda_lock_v1.CondaLockV1Loader):
            model = conda_lock_v1.CondaLockV1.model_validate(
                load_yaml(self.specifier.path)
            )
            available = tuple(model.metadata.platforms)
            missing = sorted(set(requested) - set(available))
            if missing:
                raise CondaValueError(
                    f"Platform(s) not in lockfile: {', '.join(missing)}. "
                    f"Available platforms: {', '.join(available)}"
                )

            unsupported = [
                package
                for package in model.package
                if package.platform in requested
                and (
                    package.manager != "conda"
                    or package.category != "main"
                    or package.optional
                )
            ]
            if unsupported:
                raise CondaValueError(
                    "Cannot transcode pip, optional, or non-main conda-lock-v1 "
                    "packages without losing lockfile data."
                )

            channels = tuple(
                Channel(channel.url).canonical_name
                for channel in model.metadata.channels
            )
            conda_pypi_channel = next(
                (
                    channel.url
                    for channel in model.metadata.channels
                    if Channel(channel.url).canonical_name == CONDA_PYPI_CHANNEL_NAME
                ),
                None,
            )
            environments: list[Environment] = []
            for platform in requested:
                metadata_by_url: dict[str, dict[str, Any]] = {}
                for package in model.package:
                    if package.platform != platform:
                        continue
                    if package.url in metadata_by_url:
                        raise CondaValueError(
                            "Lockfile contains duplicate package entries for one "
                            "platform."
                        )
                    if target_format == rattler_lock_v6.FORMAT and unquote(
                        urlsplit(package.url).path
                    ).endswith(".whl"):
                        raise CondaValueError(
                            "Cannot transcode conda-pypi wheel records from "
                            "conda-lock-v1 to rattler-lock-v6 without losing "
                            "package identity."
                        )
                    metadata = {
                        "depends": [
                            f"{name} {version}"
                            for name, version in package.dependencies.items()
                        ],
                        "name": package.name,
                        "version": package.version,
                        **package.hash.model_dump(exclude_none=True),
                    }
                    if conda_pypi_channel and unquote(
                        urlsplit(package.url).path
                    ).endswith(".whl"):
                        metadata["channel"] = conda_pypi_channel
                    metadata_by_url[package.url] = metadata
                environments.append(
                    Environment(
                        prefix=context.target_prefix,
                        platform=platform,
                        config=EnvironmentConfig(channels=channels),
                        explicit_packages=self.records_for_export(
                            metadata_by_url,
                            platform=platform,
                        ),
                    )
                )
        elif isinstance(self.specifier, rattler_lock_v6.RattlerLockV6Loader):
            model = rattler_lock_v6.RattlerLockV6.model_validate(
                load_yaml(self.specifier.path),
                extra="allow",
            )
            if set(model.environments) != {"default"}:
                raise CondaValueError(
                    "Cannot transcode a rattler-lock-v6 file with multiple "
                    "environments without losing lockfile data."
                )

            environment = model.environments["default"]
            available = tuple(sorted(environment.packages))
            missing = sorted(set(requested) - set(available))
            if missing:
                raise CondaValueError(
                    f"Platform(s) not in lockfile: {', '.join(missing)}. "
                    f"Available platforms: {', '.join(available)}"
                )

            # conda-lockfiles 0.2.1 ignores fields that its v6 models do not
            # declare. Reject selected data instead of silently dropping it.
            # These identity fields are redundant with the package URL and are
            # checked again when the PackageRecord is constructed.
            identity_fields = {"build", "name", "subdir", "version"}
            unsupported_fields = {
                f"lockfile.{field}"
                for field, value in (model.model_extra or {}).items()
                if value is not None and value != [] and value != {}
            }
            unsupported_fields.update(
                f"environment.{field}"
                for field, value in (environment.model_extra or {}).items()
                if value is not None and value != [] and value != {}
            )
            for channel in environment.channels:
                unsupported_fields.update(
                    f"channel.{field}"
                    for field, value in (channel.model_extra or {}).items()
                    if value is not None and value != [] and value != {}
                )

            references_by_platform = {}
            selected_keys = set()
            for platform in requested:
                references = []
                referenced_keys = set()
                for reference in environment.packages[platform]:
                    unsupported_fields.update(
                        f"reference.{field}"
                        for field, value in (reference.model_extra or {}).items()
                        if value is not None and value != [] and value != {}
                    )
                    if bool(reference.conda) == bool(reference.pypi):
                        raise CondaValueError(
                            "Rattler lock package references must identify exactly "
                            "one package manager."
                        )
                    key = (reference.package_type, reference.url)
                    if key in referenced_keys:
                        raise CondaValueError(
                            "Rattler lock contains duplicate package references "
                            "for one platform."
                        )
                    referenced_keys.add(key)
                    selected_keys.add(key)
                    references.append(reference)
                references_by_platform[platform] = tuple(references)

            packages_by_key = {}
            for package in model.packages:
                if bool(package.conda) == bool(package.pypi):
                    raise CondaValueError(
                        "Rattler lock package metadata must identify exactly one "
                        "package manager."
                    )
                key = (package.package_type, package.url)
                if key not in selected_keys:
                    continue
                package_extra = package.model_extra or {}
                unsupported_fields.update(
                    f"package.{field}"
                    for field, value in package_extra.items()
                    if value is not None
                    and value != []
                    and value != {}
                    and field not in identity_fields
                )
                if unquote(urlsplit(package.url).path).endswith(".whl"):
                    unsupported_fields.update(
                        f"package.{field}"
                        for field in identity_fields
                        if field in package_extra
                        and package_extra[field] is not None
                        and package_extra[field] != []
                        and package_extra[field] != {}
                    )
                if key in packages_by_key:
                    raise CondaValueError(
                        "Rattler lock contains duplicate package metadata."
                    )
                packages_by_key[key] = package

            if unsupported_fields:
                raise CondaValueError(
                    "Cannot transcode unsupported rattler-lock-v6 field(s): "
                    + ", ".join(sorted(unsupported_fields))
                )

            channels = tuple(
                Channel(channel.url).canonical_name for channel in environment.channels
            )
            conda_pypi_channel = next(
                (
                    channel.url
                    for channel in environment.channels
                    if Channel(channel.url).canonical_name == CONDA_PYPI_CHANNEL_NAME
                ),
                None,
            )
            environments = []
            for platform in requested:
                metadata_by_url = {}
                for reference in references_by_platform[platform]:
                    key = (reference.package_type, reference.url)
                    try:
                        package = packages_by_key[key]
                    except KeyError as exc:
                        raise CondaValueError(
                            f"{reference.package_type} package is referenced for "
                            f"platform {platform!r} but missing from the packages "
                            "list."
                        ) from exc
                    if reference.pypi:
                        raise CondaValueError(
                            "Cannot transcode rattler-lock-v6 PyPI packages without "
                            "losing lockfile data."
                        )
                    if target_format == conda_lock_v1.FORMAT and unquote(
                        urlsplit(reference.url).path
                    ).endswith(".whl"):
                        raise CondaValueError(
                            "Cannot transcode conda-pypi wheel records from "
                            "rattler-lock-v6 to conda-lock-v1 without package "
                            "metadata."
                        )
                    if target_format == conda_lock_v1.FORMAT and any(
                        (
                            package.constrains,
                            package.features,
                            package.track_features,
                            package.python_site_packages_path,
                        )
                    ):
                        raise CondaValueError(
                            "Cannot transcode rattler-lock-v6 constraints, features, "
                            "or Python site-package paths to conda-lock-v1 without "
                            "losing solver metadata."
                        )
                    if target_format == conda_lock_v1.FORMAT:
                        dependency_names = set()
                        for dependency in package.depends or ():
                            dependency_spec = MatchSpec(dependency)
                            dependency_name = dependency_spec.get_exact_value("name")
                            if dependency_name is None or any(
                                dependency_spec.get_raw_value(field) is not None
                                for field in MatchSpec.FIELD_NAMES
                                if field not in {"name", "version"}
                            ):
                                raise CondaValueError(
                                    "Cannot transcode rattler-lock-v6 dependency "
                                    "selectors to conda-lock-v1 without losing "
                                    "solver metadata."
                                )
                            if dependency_name in dependency_names:
                                raise CondaValueError(
                                    "Cannot transcode duplicate dependency names "
                                    "to conda-lock-v1 without losing constraints."
                                )
                            dependency_names.add(dependency_name)
                    package_extra = package.model_extra or {}
                    metadata = package.model_dump(
                        exclude={"conda", "pypi", *package_extra},
                        exclude_none=True,
                    )
                    metadata.update(
                        {
                            field: package_extra[field]
                            for field in identity_fields
                            if field in package_extra
                            and not unquote(urlsplit(reference.url).path).endswith(
                                ".whl"
                            )
                        }
                    )
                    if conda_pypi_channel and unquote(
                        urlsplit(reference.url).path
                    ).endswith(".whl"):
                        metadata["channel"] = conda_pypi_channel
                    metadata_by_url[reference.url] = metadata
                environments.append(
                    Environment(
                        prefix=context.target_prefix,
                        platform=platform,
                        config=EnvironmentConfig(channels=channels),
                        explicit_packages=self.records_for_export(
                            metadata_by_url,
                            platform=platform,
                        ),
                    )
                )
        else:
            return None

        return output_format.render(environments)[0]

    @staticmethod
    def records_for_export(
        metadata_by_url: dict[str, dict[str, Any]],
        *,
        platform: str,
    ) -> tuple[PackageRecord, ...]:
        """Build package records solely from metadata embedded in a lockfile."""
        records = []
        for url, metadata in metadata_by_url.items():
            try:
                filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
                if filename.endswith(".whl"):
                    try:
                        from installer.utils import parse_wheel_filename
                    except ImportError as exc:
                        raise CondaValueError(
                            "Wheel parsing support is unavailable."
                        ) from exc
                    wheel = parse_wheel_filename(filename)
                    if not wheel.tag.endswith("-none-any"):
                        raise ValueError("conda-pypi only supports pure Python wheels")
                    fields = {
                        "build": "py3_none_any_0",
                        "channel": metadata.get("channel"),
                        "fn": filename,
                        "name": wheel.distribution,
                        "subdir": "noarch",
                        "version": wheel.version,
                    }
                else:
                    spec = MatchSpec(
                        url,
                        **{
                            key: metadata[key]
                            for key in ("md5", "sha256")
                            if key in metadata
                        },
                    )
                    fields = {
                        field: spec.get_exact_value(field)
                        for field in (
                            "channel",
                            "subdir",
                            "name",
                            "version",
                            "build",
                            "fn",
                        )
                    }
                    if any(value is None for value in fields.values()):
                        raise ValueError("package URL does not contain exact metadata")
            except CondaValueError:
                raise
            except (TypeError, ValueError) as exc:
                raise CondaValueError(
                    "Unable to reconstruct a package record from a lockfile URL."
                ) from exc

            if fields["subdir"] not in {platform, "noarch"}:
                raise CondaValueError(
                    "Lockfile package URL subdir does not match its selected platform."
                )
            if not filename.endswith(".whl") and any(
                metadata.get(field) is not None and metadata[field] != fields[field]
                for field in ("build", "name", "subdir", "version")
            ):
                raise CondaValueError(
                    "Lockfile package identity does not match its URL."
                )
            records.append(
                PackageRecord(
                    **{
                        **fields,
                        "build_number": 0,
                        **metadata,
                        "url": url,
                    }
                )
            )
        return tuple(records)
