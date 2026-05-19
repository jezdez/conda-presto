"""Pip-to-conda package name translation.

Translates PyPI package names to their conda equivalents using a
three-tier lookup:

1. **cf-graph-countyfair mapping** — ~12,000 entries maintained by
   conda-forge's regro-bot.  Fetched once per process, cached in
   memory.  Authoritative source for pypi↔conda name relationships.
2. **Static fallback** — hand-maintained table for edge cases the
   upstream mapping misses or gets wrong.
3. **Normalization** — lowercase + underscore-to-hyphen, which
   handles the majority of simple cases where names only differ in
   casing or separator characters.
"""
from __future__ import annotations

import logging
import re
import threading
import urllib.request

log = logging.getLogger(__name__)

CF_GRAPH_URL = (
    "https://raw.githubusercontent.com/regro/cf-graph-countyfair/"
    "master/mappings/pypi/name_mapping.yaml"
)

_cf_mapping: dict[str, str] | None = None
_cf_lock = threading.Lock()
_cf_fetch_failed = False

STATIC_MAPPING: dict[str, str] = {
    "opencv-python-headless": "opencv",
    "opencv-contrib-python": "opencv",
    "dateutil": "python-dateutil",
    "yaml": "pyyaml",
    "attr": "attrs",
    "sklearn": "scikit-learn",
    "cv2": "py-opencv",
    "bs4": "beautifulsoup4",
    "ruamel-yaml": "ruamel.yaml",
}


def _parse_cf_yaml(raw: str) -> dict[str, str]:
    """Parse the cf-graph YAML line-by-line.

    Expects entries in the form::

        - conda_name: <name>
          import_name: <name>
          mapping_source: regro-bot
          pypi_name: <name>

    Fields may appear in any order within a block.  A block starts
    with ``- conda_name:`` and ends when the next block begins.
    """
    mapping: dict[str, str] = {}
    conda_name = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("- conda_name:"):
            conda_name = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("pypi_name:") and conda_name is not None:
            pypi_name = stripped.split(":", 1)[1].strip()
            if pypi_name and conda_name:
                mapping[pypi_name] = conda_name
            conda_name = None
    return mapping


def _fetch_cf_mapping() -> dict[str, str]:
    """Fetch and cache the cf-graph-countyfair pypi→conda mapping.

    Thread-safe: uses a lock to prevent duplicate fetches under
    concurrent requests.  On network failure, logs a warning and
    falls back to static mapping only.
    """
    global _cf_mapping, _cf_fetch_failed

    if _cf_mapping is not None:
        return _cf_mapping

    with _cf_lock:
        if _cf_mapping is not None:
            return _cf_mapping

        try:
            req = urllib.request.Request(
                CF_GRAPH_URL, headers={"User-Agent": "conda-presto"}
            )
            resp = urllib.request.urlopen(req, timeout=10)
            raw = resp.read().decode("utf-8")
        except Exception as exc:
            log.warning(
                "Failed to fetch cf-graph name mapping (%s). "
                "Name translation will use static fallback only — "
                "some packages may not be matched to their conda equivalent.",
                exc,
            )
            _cf_mapping = {}
            _cf_fetch_failed = True
            return _cf_mapping

        mapping = _parse_cf_yaml(raw)
        log.info("Loaded cf-graph mapping: %d entries", len(mapping))
        _cf_mapping = mapping
        return _cf_mapping


def cf_mapping_available() -> bool:
    """Return whether the cf-graph mapping was loaded successfully."""
    return _cf_mapping is not None and not _cf_fetch_failed


def normalize_pip_name(name: str) -> str:
    """Normalize a pip package name for conda lookup.

    Strips extras (``requests[security]`` → ``requests``), lowercases,
    and replaces underscores with hyphens.
    """
    name = re.sub(r"\[.*\]", "", name)
    return name.lower().replace("_", "-").strip()


def get_conda_name(pip_name: str) -> str:
    """Return the conda package name for a pip package.

    Lookup order: cf-graph mapping → static fallback → normalized name.
    """
    cf_mapping = _fetch_cf_mapping()

    if pip_name in cf_mapping:
        return cf_mapping[pip_name]

    normalized = normalize_pip_name(pip_name)
    if normalized in cf_mapping:
        return cf_mapping[normalized]

    if normalized in STATIC_MAPPING:
        return STATIC_MAPPING[normalized]

    return normalized
