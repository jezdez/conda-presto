"""Centralized exception types and error-sanitization helpers.

Keeping these in one place makes it easy to audit which error types
conda-presto defines (currently just :class:`UnknownFormatError`) and
which known-safe exception types it re-surfaces to HTTP clients.

``SAFE_ERROR_TYPES`` is the allow-list of exception classes whose
``str(exc)`` is considered user-actionable and safe to return to
clients.  Anything not in the list is sanitized via
:func:`safe_error_message` to a generic message; full detail still
lands in the server logs.
"""
from __future__ import annotations

import re

from conda.exceptions import PackagesNotFoundError, UnsatisfiableError


class UnknownFormatError(ValueError):
    """Raised when a requested exporter format name is not registered.

    Carries ``format_name`` and the sorted list of ``available`` format
    names so callers can surface a helpful error to the user.
    """

    def __init__(self, format_name: str, available: list[str]) -> None:
        self.format_name = format_name
        self.available = available
        msg = f"Unknown format {format_name!r}"
        if available:
            msg += f"; available: {', '.join(available)}"
        super().__init__(msg)


SAFE_ERROR_TYPES: tuple[type[Exception], ...] = (
    UnsatisfiableError,
    PackagesNotFoundError,
)

URL_RE = re.compile(r"https?://[^\s)]+")


def safe_error_message(exc: Exception) -> str:
    """Return a user-safe error message for *exc*.

    Known solver errors surface request-level detail (they're
    user-actionable), with configured channel URLs redacted. Everything
    else returns a generic message so that internal paths, stack traces,
    or library internals don't leak to clients.
    """
    if isinstance(exc, SAFE_ERROR_TYPES):
        return redact_safe_error(str(exc))
    return "Internal solver error"


def redact_safe_error(message: str) -> str:
    """Redact channel and URL details from a known user-facing error."""
    return URL_RE.sub("[redacted-url]", redact_current_channels(message))


def redact_current_channels(message: str) -> str:
    """Replace conda's ``Current channels`` block with a placeholder."""
    redacted: list[str] = []
    lines = message.splitlines()
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if line.strip() == "Current channels:":
            redacted.append("Current channels: [redacted]")
            idx += 1
            while idx < len(lines) and (
                not lines[idx].strip() or lines[idx].startswith("  - ")
            ):
                idx += 1
            continue
        redacted.append(line)
        idx += 1

    return "\n".join(redacted)
