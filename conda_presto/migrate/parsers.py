"""Pip environment file parsing.

Handles the pip-ecosystem input formats that conda's built-in env-spec
plugins do not understand natively:

- ``requirements.txt`` (pip format with extras, URLs, hashes)
- ``pyproject.toml`` ([project].dependencies)
- ``Pipfile`` ([packages] section)
- ``poetry.lock`` ([[package]] entries)

Each parser returns a flat ``dict[str, str]`` mapping package names to
version specifiers (empty string for unpinned).  Format detection is
content-based so callers don't need to know the format up front.
"""
from __future__ import annotations

import tomllib

from packaging.requirements import InvalidRequirement, Requirement


def detect_format(content: str) -> str:
    """Auto-detect input file format from content structure."""
    if "[project]" in content and "dependencies" in content:
        return "pyproject"
    if "[packages]" in content and "[dev-packages]" in content:
        return "pipfile"
    if "[[package]]" in content:
        return "poetry"
    return "requirements"


def parse_requirements_txt(content: str) -> dict[str, str]:
    """Parse requirements.txt into ``{name: version_spec}``.

    Skips comments, option lines (``-r``, ``-e``, ``--index-url``, etc.),
    and unparseable lines (URLs, local paths).
    """
    packages: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            req = Requirement(line)
            packages[req.name] = str(req.specifier) if req.specifier else ""
        except InvalidRequirement:
            continue
    return packages


def parse_pyproject_toml(content: str) -> dict[str, str]:
    """Parse ``[project].dependencies`` from a pyproject.toml."""
    data = tomllib.loads(content)
    deps = data.get("project", {}).get("dependencies", [])
    packages: dict[str, str] = {}
    for dep in deps:
        try:
            req = Requirement(dep)
            packages[req.name] = str(req.specifier) if req.specifier else ""
        except InvalidRequirement:
            continue
    return packages


def parse_pipfile(content: str) -> dict[str, str]:
    """Parse ``[packages]`` section from a Pipfile."""
    data = tomllib.loads(content)
    raw = data.get("packages", {})
    packages: dict[str, str] = {}
    for name, spec in raw.items():
        if isinstance(spec, str):
            packages[name] = spec.lstrip("=~!<>") if spec != "*" else ""
        elif isinstance(spec, dict):
            packages[name] = spec.get("version", "").lstrip("=~!<>")
        else:
            packages[name] = ""
    return packages


def parse_poetry_lock(content: str) -> dict[str, str]:
    """Parse ``[[package]]`` entries from a poetry.lock."""
    data = tomllib.loads(content)
    packages: dict[str, str] = {}
    for pkg in data.get("package", []):
        name = pkg.get("name", "")
        version = pkg.get("version", "")
        if name:
            packages[name] = f"=={version}" if version else ""
    return packages


def parse_spec(content: str, fmt: str = "auto") -> dict[str, str]:
    """Parse any supported pip format into ``{name: version_spec}``.

    When *fmt* is ``"auto"``, the format is detected from the content.
    """
    if fmt == "auto":
        fmt = detect_format(content)

    parsers = {
        "requirements": parse_requirements_txt,
        "pyproject": parse_pyproject_toml,
        "pipfile": parse_pipfile,
        "poetry": parse_poetry_lock,
    }
    parser = parsers.get(fmt)
    if parser is None:
        raise ValueError(f"Unknown format {fmt!r}")
    return parser(content)
