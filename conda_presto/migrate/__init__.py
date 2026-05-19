"""Pip-to-conda environment migration.

Parses pip requirement files (requirements.txt, pyproject.toml, Pipfile,
poetry.lock), translates package names to their conda equivalents, checks
per-package availability, and validates the result via conda-presto's
solver.

Usage::

    from conda_presto.migrate import migrate

    result = migrate(
        spec_content=open("requirements.txt").read(),
        channels=["conda-forge"],
        platform="osx-arm64",
    )
    for pkg in result.packages:
        print(f"{pkg.pip_name} -> {pkg.conda_name} [{pkg.status}]")
    print(f"Solve: {'OK' if result.solve_success else result.solve_error}")
"""
from __future__ import annotations

import logging

from ..resolve import NATIVE_SUBDIR, solve_one_platform
from .models import MappedPackage, MigrationResult
from .name_mapping import get_conda_name
from .parsers import detect_format, parse_spec
from .renderer import render_environment_yml

log = logging.getLogger(__name__)

__all__ = ["migrate"]


PROBE_PLATFORMS = ["linux-64", "osx-arm64", "win-64"]


def _classify_unavailable(
    pkg: MappedPackage,
    channels: tuple[str, ...],
    platform: str,
) -> None:
    """Classify why a package is unavailable and mutate *pkg* in place.

    Graduated probes using only the solver:
    1. Drop version constraint, solve on target platform.
       Succeeds → "version_not_available" (version gap).
    2. Solve bare name on other platforms.
       Succeeds → "wrong_arch" (exists elsewhere, not here).
    3. All fail → "not_in_conda" (truly pip-only).
    """
    pkg.status = "unavailable"

    # Probe 1: does the package exist at ALL on target platform?
    bare_result = solve_one_platform(channels, [pkg.conda_name], platform)
    if bare_result.error is None:
        pkg.reason = "version_not_available"
        # Extract what version was resolved so user sees alternatives
        for resolved in bare_result.packages:
            if resolved.name == pkg.conda_name:
                pkg.available_versions = [resolved.version]
                break
        return

    # Probe 2: does it exist on a different platform?
    for other_platform in PROBE_PLATFORMS:
        if other_platform == platform:
            continue
        cross_result = solve_one_platform(channels, [pkg.conda_name], other_platform)
        if cross_result.error is None:
            pkg.reason = "wrong_arch"
            return

    # Nothing found anywhere
    pkg.reason = "not_in_conda"


def migrate(
    spec_content: str,
    channels: list[str] | None = None,
    platform: str | None = None,
    fmt: str = "auto",
) -> MigrationResult:
    """Migrate a pip spec file to conda, validating via dry-run solve.

    Parameters
    ----------
    spec_content:
        The raw text content of a pip spec file.
    channels:
        Conda channels to resolve against.  Defaults to ``["conda-forge"]``.
    platform:
        Target platform (e.g. ``"linux-64"``).  Defaults to current.
    fmt:
        Input format hint (``"auto"`` to detect).

    Returns
    -------
    MigrationResult
        Contains per-package status and solve outcome for available packages.
    """
    if channels is None:
        channels = ["conda-forge"]
    if platform is None:
        platform = NATIVE_SUBDIR

    ch = tuple(channels)

    # 1. Parse
    detected_fmt = fmt if fmt != "auto" else detect_format(spec_content)
    pip_packages = parse_spec(spec_content, detected_fmt)
    log.info("Parsed %d packages from %s input", len(pip_packages), detected_fmt)

    # 2. Map pip names → conda names
    mapped: list[MappedPackage] = []
    for pip_name, version_spec in pip_packages.items():
        conda_name = get_conda_name(pip_name)
        conda_spec = f"{conda_name} {version_spec}" if version_spec else conda_name
        mapped.append(MappedPackage(
            pip_name=pip_name,
            pip_version_spec=version_spec,
            conda_name=conda_name,
            conda_spec=conda_spec,
        ))

    log.info("Mapped %d packages, attempting bulk solve on %s", len(mapped), platform)

    def _build_result(solve_success: bool, solve_error: str | None = None) -> MigrationResult:
        yml = render_environment_yml(
            MigrationResult(
                platform=platform,
                source_format=detected_fmt,
                packages=mapped,
                solve_success=solve_success,
                solve_error=solve_error,
            ),
            channels=channels,
        )
        return MigrationResult(
            platform=platform,
            source_format=detected_fmt,
            packages=mapped,
            solve_success=solve_success,
            solve_error=solve_error,
            environment_yml=yml,
        )

    if not mapped:
        return _build_result(solve_success=True)

    # Suppress solver warnings during probing — we handle errors ourselves
    resolve_log = logging.getLogger("conda_presto.resolve")
    original_level = resolve_log.level

    # 3. Try bulk solve first (fast path — all packages at once)
    all_specs = [pkg.conda_spec for pkg in mapped]
    resolve_log.setLevel(logging.CRITICAL)
    bulk_result = solve_one_platform(ch, all_specs, platform)
    resolve_log.setLevel(original_level)

    if bulk_result.error is None:
        log.info("Bulk solve succeeded: %d packages", len(bulk_result.packages))
        return _build_result(solve_success=True)

    # 4. Bulk solve failed — classify each package
    log.info("Bulk solve failed, classifying individual packages")
    resolve_log.setLevel(logging.CRITICAL)
    for pkg in mapped:
        single_result = solve_one_platform(ch, [pkg.conda_spec], platform)
        if single_result.error is not None:
            _classify_unavailable(pkg, ch, platform)
            log.info(
                "  %s (%s): %s", pkg.pip_name, pkg.conda_name, pkg.reason
            )
    resolve_log.setLevel(original_level)

    # 5. Re-solve with only available packages
    available_specs = [pkg.conda_spec for pkg in mapped if pkg.status == "available"]
    available_count = len(available_specs)
    unavailable_count = len(mapped) - available_count
    log.info(
        "%d available, %d unavailable — re-solving",
        available_count, unavailable_count,
    )

    if not available_specs:
        return _build_result(
            solve_success=False,
            solve_error="No packages available on target channels",
        )

    final_result = solve_one_platform(ch, available_specs, platform)

    if final_result.error:
        log.warning("Re-solve failed: %s", final_result.error)
        return _build_result(solve_success=False, solve_error=final_result.error)

    log.info("Re-solve succeeded: %d packages resolved", len(final_result.packages))
    return _build_result(solve_success=True)
