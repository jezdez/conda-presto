"""Data models for migration results.

Uses ``msgspec.Struct`` to match conda-presto's conventions: fast
instantiation, low memory, and native JSON encoding via Litestar
without intermediate dict conversion.
"""
from __future__ import annotations

import msgspec


class MappedPackage(msgspec.Struct):
    """A single pip package mapped to its conda equivalent.

    ``status`` is one of:
    - ``"available"`` — found on target channels, included in conda section
    - ``"unavailable"`` — not on target channels, falls to pip section

    When ``status == "unavailable"``, ``reason`` explains why:
    - ``"version_not_available"`` — package exists but not the requested version
    - ``"wrong_arch"`` — package exists on other platforms but not the target
    - ``"not_in_conda"`` — package doesn't exist on any configured channel
    """

    pip_name: str
    pip_version_spec: str
    conda_name: str
    conda_spec: str
    status: str = "available"
    reason: str | None = None
    available_versions: list[str] | None = None


class MigrationResult(msgspec.Struct):
    """Result of a full migration.

    Migration always produces a usable ``environment_yml`` — every
    package lands either in the conda section
    (``status == "available"``) or the pip fallback section
    (``status == "unavailable"``).

    ``solve_success`` indicates whether the **conda section** solved
    cleanly.  When ``False``, the conda packages had conflicts (see
    ``solve_error``).  The pip section is pass-through and always
    valid.  Even when ``solve_success`` is ``False``, the
    ``environment_yml`` is populated with available packages that
    did solve individually.
    """

    platform: str
    source_format: str
    packages: list[MappedPackage]
    solve_success: bool
    solve_error: str | None = None
    environment_yml: str = ""
