"""Centralized exception types and error-sanitization helpers.

Keeping these in one place makes it easy to audit which error types
conda-presto defines and
which known-safe exception types it re-surfaces to HTTP clients.

``SAFE_ERROR_TYPES`` is the allow-list of exception classes whose
``str(exc)`` is considered user-actionable and safe to return to
clients.  Anything not in the list is sanitized via
:func:`safe_error_message` to a generic message. Full detail still
lands in the server logs.
"""

from __future__ import annotations

import logging
import re
import traceback
from itertools import chain
from urllib.parse import parse_qsl, unquote, urlparse

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


class WorkspaceSolveError(RuntimeError):
    """Identify a failed workspace environment and target with safe detail."""

    def __init__(self, environment: str, platform: str, error: str) -> None:
        self.environment = environment
        self.platform = platform
        self.error = error
        super().__init__(f"Environment {environment!r} on {platform!r}: {error}")


# Start once per scheme-character run. Possessive repeats alone would still
# retry at every position in a long failed scheme prefix.
URL_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])(?P<prefix>[0-9+.-]*+)"
    r"(?P<url>[A-Za-z][A-Za-z0-9+.-]*+://\S+)"
)
CREDENTIAL_URL_RE = re.compile(
    r"(?<![A-Za-z0-9+.-])[0-9+.-]*+"
    r"""(?P<url>[A-Za-z][A-Za-z0-9+.-]*+://[^\s"'<>\\,{}]+)"""
)


def contains_credentials(value: object) -> bool:
    """Return whether nested data contains known credential patterns."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str) and key.lower() in {
                    "auth",
                    "password",
                    "token",
                }:
                    if child:
                        return True
                pending.append(child)
        elif isinstance(item, (list, tuple, set)):
            pending.extend(item)
        elif isinstance(item, bytes):
            pending.append(item.decode("utf-8", errors="replace"))
        elif isinstance(item, str):
            decoded = item
            for _ in range(3):
                next_value = unquote(decoded)
                if next_value == decoded:
                    break
                decoded = next_value
            if "%" in decoded:
                return True
            for candidate in chain(
                (decoded,),
                (match["url"] for match in CREDENTIAL_URL_RE.finditer(decoded)),
            ):
                try:
                    parsed = urlparse(candidate)
                except ValueError:
                    return True
                if parsed.scheme in {"pkg", "conda-environment"} and not parsed.netloc:
                    # PURL qualifiers and environment references are identifiers.
                    # Their values can still contain credentialed download URLs.
                    for name, qualifier in parse_qsl(parsed.query):
                        if name.lower() in {"auth", "password", "token"} and qualifier:
                            return True
                        pending.append(qualifier)
                    pending.append(parsed.fragment)
                    continue
                path_parts = [part for part in parsed.path.split("/") if part]
                if (
                    parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or "t" in path_parts[:-1]
                ):
                    return True
    return False


class CredentialRedactionFilter(logging.Filter):
    """Redact URLs from dependency logs before they leave a solve process."""

    @classmethod
    def install(cls) -> None:
        """Attach one filter to every handler configured in this process."""
        handlers = set(logging.getLogger().handlers)
        if logging.lastResort is not None:
            handlers.add(logging.lastResort)
        for logger in logging.root.manager.loggerDict.values():
            if isinstance(logger, logging.Logger):
                handlers.update(logger.handlers)
        for handler in handlers:
            if not any(isinstance(filter_, cls) for filter_ in handler.filters):
                handler.addFilter(cls())

    def filter(self, record: logging.LogRecord) -> bool:
        """Render and redact the complete record before formatting."""
        message = record.getMessage()
        if record.exc_info:
            message = "\n".join(
                (message, "".join(traceback.format_exception(*record.exc_info)))
            )
        record.msg = redact_safe_error(message)
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


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

    return URL_RE.sub(r"\g<prefix>[redacted-url]", "\n".join(redacted))
