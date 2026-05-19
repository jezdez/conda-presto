"""Render migration results as a valid environment.yml.

Follows the official Anaconda guidance for mixing pip and conda:
https://www.anaconda.com/docs/getting-started/working-with-conda/packages/pip-install

- Channels listed explicitly so ``conda env create`` uses the right source
- Conda packages listed first in ``dependencies:``
- ``pip`` installed as a conda dependency
- Pip-only packages listed last under ``pip:`` subsection

Python version is only included if the input spec explicitly pins it.
Otherwise the solver picks the latest compatible version.
"""
from __future__ import annotations

from .models import MigrationResult


def render_environment_yml(
    result: MigrationResult,
    name: str = "migrated",
    channels: list[str] | None = None,
) -> str:
    """Render a MigrationResult as a valid environment.yml string.

    Parameters
    ----------
    result:
        Migration result with classified packages.
    name:
        Environment name in the output YAML.
    channels:
        Channels to include. Defaults to ``["conda-forge"]``.
    """
    if channels is None:
        channels = ["conda-forge"]

    conda_pkgs = [p for p in result.packages if p.status == "available"]
    pip_pkgs = [p for p in result.packages if p.status != "available"]

    lines: list[str] = []
    lines.append(f"name: {name}")
    lines.append("channels:")
    for ch in channels:
        lines.append(f"  - {ch}")
    lines.append("dependencies:")

    if not conda_pkgs and not pip_pkgs:
        lines.append("  []")
        lines.append("")
        return "\n".join(lines)

    for pkg in conda_pkgs:
        spec = pkg.conda_name
        if pkg.pip_version_spec:
            spec += " " + pkg.pip_version_spec
        lines.append(f"  - {spec}")

    if pip_pkgs:
        lines.append("  - pip")
        lines.append("  - pip:")
        for pkg in pip_pkgs:
            spec = pkg.pip_name
            if pkg.pip_version_spec:
                spec += pkg.pip_version_spec
            lines.append(f"    - {spec}")

    lines.append("")
    return "\n".join(lines)
