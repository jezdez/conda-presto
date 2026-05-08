"""Application configuration via environment variables.

All settings have sensible defaults and can be overridden by setting
the corresponding ``CONDA_PRESTO_*`` environment variable.

Channels and platforms:
    ``CONDA_PRESTO_CHANNELS``
        Comma-separated fallback channels (default: ``conda-forge``).
    ``CONDA_PRESTO_PLATFORMS``
        Comma-separated platforms to pre-warm on startup
        (default: ``linux-64,osx-arm64,osx-64``).

Server tuning:
    ``CONDA_PRESTO_CONCURRENCY``
        Max concurrent solve requests via thread limiter (default: ``4``).
    ``CONDA_PRESTO_WORKERS``
        Process pool size for multi-platform solves
        (default: ``min(4, cpu_count)``).
    ``CONDA_PRESTO_MAX_BODY_BYTES``
        Max request body size in bytes (default: ``1048576``).
    ``CONDA_PRESTO_HOST``
        Default bind address for ``--serve`` (default: ``127.0.0.1``).
    ``CONDA_PRESTO_PORT``
        Default port for ``--serve`` (default: ``8000``).

Request limits (abuse/DoS protection):
    ``CONDA_PRESTO_SOLVE_TIMEOUT_S``
        Max wall-clock seconds per solve request (default: ``60``).
        Returns HTTP 504 if exceeded.
    ``CONDA_PRESTO_MAX_PLATFORMS``
        Max platforms per request (default: ``8``).  Returns HTTP 400
        if exceeded.
    ``CONDA_PRESTO_MAX_SPECS``
        Max specs per request (default: ``200``).  Returns HTTP 400
        if exceeded.

HTTP middleware:
    ``CONDA_PRESTO_RATE_LIMIT``
        Max requests per minute per client (default: ``300``).
        Set to ``0`` to disable rate limiting.  Behind a reverse proxy,
        start uvicorn with ``--forwarded-allow-ips`` so the client IP
        is taken from ``X-Forwarded-For`` rather than the proxy.
    ``CONDA_PRESTO_CORS_ORIGINS``
        Comma-separated allowed CORS origins (default: ``*``).
    ``CONDA_PRESTO_LOG_LEVEL``
        Application log level (default: ``INFO``).

Solve receipts:
    ``CONDA_PRESTO_RECEIPT_SECRET``
        HMAC secret for signing solve receipts.  If unset, a random
        32-byte secret is generated at import time (receipts will not
        survive server restarts).
    ``CONDA_PRESTO_RECEIPTS``
        Enable or disable receipt emission (default: ``true``).

Cross-platform virtual packages:
    ``CONDA_PRESTO_GLIBC_VERSION``
        Virtual ``__glibc`` version for Linux solves (default: ``2.17``).
    ``CONDA_PRESTO_LINUX_VERSION``
        Virtual ``__linux`` version for Linux solves (default: ``5.15``).
    ``CONDA_PRESTO_OSX_VERSION``
        Virtual ``__osx`` version for macOS solves (default: ``11.0``).
    ``CONDA_PRESTO_WIN_VERSION``
        Virtual ``__win`` version for Windows solves (default: ``0``).
        The ``__win`` virtual package is usually unversioned; the value
        exists to keep the override dict shape consistent.
"""

from __future__ import annotations

import logging
import os
import secrets


def env_int(name: str, default: int) -> int:
    """Parse an integer env var.  Raises with a clear message on bad input."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {name}: {raw!r}") from exc


def env_list(name: str, default: str) -> list[str]:
    """Parse a comma-separated env var, stripping whitespace and empties."""
    return [
        part.strip()
        for part in os.environ.get(name, default).split(",")
        if part.strip()
    ]


DEFAULT_CHANNELS = env_list("CONDA_PRESTO_CHANNELS", "conda-forge")

DEFAULT_PLATFORMS = env_list("CONDA_PRESTO_PLATFORMS", "linux-64,osx-arm64,osx-64")

MAX_BODY_BYTES = env_int("CONDA_PRESTO_MAX_BODY_BYTES", 1_024 * 1_024)

MAX_CONCURRENCY = env_int("CONDA_PRESTO_CONCURRENCY", 4)

MAX_WORKERS = env_int(
    "CONDA_PRESTO_WORKERS",
    min(4, os.cpu_count() or 4),
)

GLIBC_VERSION = os.environ.get("CONDA_PRESTO_GLIBC_VERSION", "2.17")
LINUX_VERSION = os.environ.get("CONDA_PRESTO_LINUX_VERSION", "5.15")
OSX_VERSION = os.environ.get("CONDA_PRESTO_OSX_VERSION", "11.0")
WIN_VERSION = os.environ.get("CONDA_PRESTO_WIN_VERSION", "0")

DEFAULT_HOST = os.environ.get("CONDA_PRESTO_HOST", "127.0.0.1")
DEFAULT_PORT = env_int("CONDA_PRESTO_PORT", 8000)

RATE_LIMIT = env_int("CONDA_PRESTO_RATE_LIMIT", 300)
CORS_ORIGINS = env_list("CONDA_PRESTO_CORS_ORIGINS", "*")
LOG_LEVEL = os.environ.get("CONDA_PRESTO_LOG_LEVEL", "INFO")

SOLVE_TIMEOUT_S = env_int("CONDA_PRESTO_SOLVE_TIMEOUT_S", 60)
MAX_PLATFORMS = env_int("CONDA_PRESTO_MAX_PLATFORMS", 8)
MAX_SPECS = env_int("CONDA_PRESTO_MAX_SPECS", 200)

RESULT_CACHE_MAX_ENTRIES = env_int("CONDA_PRESTO_RESULT_CACHE_MAX_ENTRIES", 1000)
RESULT_CACHE_ENABLED = os.environ.get("CONDA_PRESTO_RESULT_CACHE", "true").lower() in (
    "true",
    "1",
    "yes",
)

RECEIPT_SECRET = os.environ.get("CONDA_PRESTO_RECEIPT_SECRET", "").encode("utf-8")
RECEIPT_ENABLED = os.environ.get("CONDA_PRESTO_RECEIPTS", "true").lower() in (
    "true",
    "1",
    "yes",
)

if not RECEIPT_SECRET:
    RECEIPT_SECRET = secrets.token_bytes(32)
    logging.getLogger(__name__).warning(
        "CONDA_PRESTO_RECEIPT_SECRET not set, using random secret. "
        "Receipts will not survive server restarts."
    )
