"""Litestar HTTP API for conda environment resolving.

Endpoints:

- ``GET /resolve`` — resolve specs via query params
- ``POST /resolve`` — resolve specs and/or file content via JSON body
- ``POST /preflight`` — validate input locally without solving
- ``POST /repair`` — suggest relaxations for infeasible specs
- ``POST /diff`` — compare two resolved inputs
- ``POST /explain`` — show dependency chains for one resolved package
- ``POST /transcode`` — convert one lockfile format to another
- ``GET /r/{hash}`` — fetch a stored content-addressed resolve result
- ``GET /formats`` — list registered output format names
- ``GET /platforms`` — list known conda platform subdirs
- ``GET /version`` — version info for conda-presto and dependencies
- ``POST /parse`` — extract specs/channels from a file without solving
- ``GET /health`` — reports solver readiness
- ``GET /`` — first-party browser workbench
- ``GET /openapi.json`` — OpenAPI 3.1 schema (auto-generated)

Output formats:
    By default, ``/resolve`` returns a list of ``SolveResult`` objects
    serialized as JSON via msgspec, with per-platform ``error`` fields
    for partial failures.

    Passing ``?format=<name>`` routes through conda's exporter plugin
    registry instead, returning whatever the named exporter produces
    (``explicit``, ``environment-yaml``, ``environment-json``, and —
    when ``conda-lockfiles`` is installed — ``conda-lock-v1`` and
    ``rattler-lock-v6``/``pixi-lock-v6``).  The response
    ``Content-Type`` is derived from the exporter's default filename
    extension (``application/yaml``, ``application/json``, or
    ``text/plain``).  Unknown format names return HTTP 400 with the
    list of available formats.  Solver failures on any platform
    propagate as HTTP 500 on this path, because exporters operate on
    successful ``Environment`` objects only.

Configuration is loaded from environment variables via
:mod:`conda_presto.config`.  See that module for the full list of
``CONDA_PRESTO_*`` settings (default channels, concurrency limits,
CORS, rate limiting, log level, request caps, etc.).

Security design:
    - File content is written to a temp file with a whitelisted extension
      and processed through conda's env spec plugin system (same as CLI).
    - Path traversal is prevented by stripping directory components from
      the client-provided filename.
    - Solver errors are wrapped via
      :func:`conda_presto.exceptions.safe_error_message` so only an
      allow-list of known errors surfaces detail to clients; everything
      else returns a generic message with full detail in server logs.
    - Rate limiting (configurable via ``CONDA_PRESTO_RATE_LIMIT``,
      default 300 req/min) follows the IETF RateLimit draft headers.
    - Per-request caps (``CONDA_PRESTO_MAX_SPECS``,
      ``CONDA_PRESTO_MAX_PLATFORMS``) and a solve timeout
      (``CONDA_PRESTO_SOLVE_TIMEOUT_S``) limit abuse and runaway solves.

Performance design:
    - All solve calls run off the event loop via ``anyio.to_thread``
      with a concurrency limit of ``MAX_CONCURRENCY`` (configurable via
      ``CONDA_PRESTO_CONCURRENCY``).
    - The solver-resource lifespan pre-warms repodata caches so the first
      request doesn't pay cold-start costs.
    - Successful ``/resolve`` responses are stored in a content-addressed
      result cache and can be fetched again from ``/r/{hash}`` while the
      backing cache entry remains available.
    - Response compression (brotli with gzip fallback) reduces
      bandwidth for large solve results.
    - ``SolveResult`` / ``ResolvedPackage`` are ``msgspec.Struct`` and
      returned directly; Litestar encodes them natively without an
      intermediate dict conversion.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field, replace
from functools import partial
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import anyio
import msgspec
from conda.base.constants import KNOWN_SUBDIRS
from conda.exceptions import CondaError
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from litestar import Litestar, Request, get, post
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.connection import ASGIConnection
from litestar.datastructures import CacheControlHeader
from litestar.enums import RequestEncodingType
from litestar.exceptions import NotFoundException
from litestar.handlers import BaseRouteHandler
from litestar.logging import LoggingConfig
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig, ResponseSpec
from litestar.openapi.plugins import JsonRenderPlugin
from litestar.params import (
    FromPath,
    FromQuery,
    HeaderParameter,
    QueryParameter,
    URLEncodedBody,
)
from litestar.response import Response, Template
from litestar.static_files import create_static_files_router
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_503_SERVICE_UNAVAILABLE,
    HTTP_504_GATEWAY_TIMEOUT,
)
from litestar.template.config import TemplateConfig

try:
    from litestar.plugins.jinja import JinjaTemplateEngine
except ImportError:  # pragma: no cover - compatibility with Litestar 2.18-2.21
    from litestar.contrib.jinja import JinjaTemplateEngine

from .cache import ResultCache, SolverResultService
from .config import (
    CHANNEL_ALLOWLIST,
    CORS_ORIGINS,
    DEFAULT_CHANNELS,
    DEFAULT_PLATFORMS,
    LOG_LEVEL,
    MAX_BODY_BYTES,
    MAX_CHANNELS,
    MAX_CONCURRENCY,
    MAX_PLATFORMS,
    MAX_REPAIR_ATTEMPTS,
    MAX_REPAIR_SUGGESTIONS,
    MAX_REPAIR_TIME_BUDGET_MS,
    MAX_SOLVER_CHANNELS,
    MAX_SOLVER_STATE_ITEMS,
    MAX_SPECS,
    PARSE_TIMEOUT_S,
    PERSISTENT_WORKER,
    RATE_LIMIT,
    RESULT_CACHE_BACKEND,
    RESULT_CACHE_DIR,
    RESULT_CACHE_MAX_MEMORY_BYTES,
    RESULT_CACHE_REDIS_NAMESPACE,
    RESULT_CACHE_REDIS_URL,
    RESULT_CACHE_SIZE,
    SOLVE_TIMEOUT_S,
    SOLVER_CACHE_WARM_BATCH_SIZE,
    SOLVER_CACHE_WARM_CANDIDATE_PERSIST,
    SOLVER_CACHE_WARM_CANDIDATE_SIZE,
    SOLVER_CACHE_WARM_INTERVAL_S,
)
from .exceptions import SAFE_ERROR_TYPES, CredentialRedactionFilter, UnknownFormatError
from .exporter import ExporterCacheIdentity, OutputFormat
from .inputs import ParsedInputFile
from .preflight import PreflightResult
from .resolve import (
    NATIVE_SUBDIR,
    ExplainResult,
    PlatformDiff,
    RepodataSnapshot,
    SolveResult,
    shutdown_process_pool,
    solve,
    solve_environments,
    warmup,
)
from .solver import (
    PrestoSolveError,
    PrestoSolverClient,
    PrestoSolveRequest,
)
from .storage import StoreOperationCoordinator
from .warm_candidates import SolverWarmCandidates
from .warmer import ForegroundCapacity, SolverCacheWarmer
from .worker import PersistentSolveWorker

log = logging.getLogger(__name__)

RAW_CONTENT_TYPE_EXTENSIONS: dict[str, str] = {
    "application/yaml": ".yml",
    "application/x-yaml": ".yml",
    "text/yaml": ".yml",
    "text/x-yaml": ".yml",
    "application/toml": ".toml",
    "application/x-toml": ".toml",
    "text/toml": ".toml",
    "text/plain": ".txt",
}
PACKAGE_DIR = Path(__file__).parent
WORKBENCH_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class ErrorResponse(msgspec.Struct, omit_defaults=True):
    """A client-facing error payload."""

    error: str
    platform: str | None = None
    supported: list[str] | None = None


class HealthResponse(msgspec.Struct):
    """Solver readiness payload."""

    status: Literal["ok", "unavailable"]


@dataclass
class ResolveRequest:
    """JSON body for ``POST /resolve`` (Content-Type: application/json).

    Fields default to ``None`` (not present) rather than empty lists so
    that the handler can use presence-based override semantics: an
    explicit empty array in the body overrides any query-param value,
    while an omitted field falls through to the query params.
    """

    specs: list[str] | None = None
    file: str | None = None
    filename: str | None = None
    channels: list[str] | None = None
    platforms: list[str] | None = None

    @classmethod
    async def from_http(
        cls,
        request: Request,
        spec: list[str] | None = None,
        channel: list[str] | None = None,
        platform: list[str] | None = None,
        filename: str | None = None,
    ) -> ResolveRequest | Response:
        """Decode the JSON or raw-file request shape shared by resolve surfaces."""
        content_type, _ = request.content_type

        if content_type in ("", "application/json"):
            body = await request.body()
            if body:
                try:
                    data = msgspec.json.decode(body, type=cls)
                except (msgspec.DecodeError, msgspec.ValidationError) as exc:
                    return Response(
                        ErrorResponse(error=f"Invalid JSON body: {exc}"),
                        status_code=HTTP_400_BAD_REQUEST,
                    )
            else:
                data = cls()
            return cls(
                specs=data.specs if data.specs is not None else (spec or []),
                channels=(
                    data.channels if data.channels is not None else (channel or [])
                ),
                platforms=(
                    data.platforms if data.platforms is not None else (platform or [])
                ),
                file=data.file,
                filename=data.filename or filename,
            )

        if content_type in RAW_CONTENT_TYPE_EXTENSIONS:
            body = await request.body()
            try:
                content = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                return Response(
                    ErrorResponse(error=f"Body is not valid UTF-8: {exc}"),
                    status_code=HTTP_400_BAD_REQUEST,
                )
            return cls(
                specs=spec or [],
                channels=channel or [],
                platforms=platform or [],
                file=content,
                filename=filename
                or f"environment{RAW_CONTENT_TYPE_EXTENSIONS[content_type]}",
            )

        return Response(
            ErrorResponse(
                error=(
                    f"Unsupported Content-Type {content_type!r}. "
                    "Use application/json for a ResolveRequest envelope, "
                    "or application/yaml / application/toml / text/plain "
                    "for a raw input file body."
                ),
                supported=["application/json", *sorted(RAW_CONTENT_TYPE_EXTENSIONS)],
            ),
            status_code=HTTP_400_BAD_REQUEST,
        )

    async def preflight(self, request: Request) -> PreflightResult | Response:
        """Validate this request without solving or channel access."""
        specs = list(self.specs or [])
        channels = list(self.channels or [])
        if self.file is not None:
            parsed = await parse_input_for_request(
                request,
                self.file,
                self.filename,
                self.platforms or [NATIVE_SUBDIR],
            )
            if isinstance(parsed, Response):
                if parsed.status_code != HTTP_400_BAD_REQUEST:
                    return parsed
                return PreflightResult.from_values(
                    specs,
                    channels,
                    self.file,
                    str(parsed.content["error"]),
                )
            specs.extend(parsed.specs)
            if not channels:
                channels = parsed.channels

        if cap_error := validate_caps(
            specs,
            channels,
            self.platforms or [],
            validate_channel_allowlist=False,
        ):
            return cap_error
        return PreflightResult.from_values(specs, channels, self.file)


@dataclass
class WorkbenchForm:
    """URL-encoded fields submitted by the browser workbench."""

    specs: str = ""
    channels: str = ""
    platforms: str = ""
    file: str = ""
    filename: str = ""
    format: str = ""

    @property
    def spec_values(self) -> list[str]:
        """Return one package spec per non-empty line."""
        return [value.strip() for value in self.specs.splitlines() if value.strip()]

    @property
    def channel_values(self) -> list[str]:
        """Return comma- or line-separated channel values."""
        return [
            value.strip()
            for line in self.channels.splitlines()
            for value in line.split(",")
            if value.strip()
        ]

    @property
    def platform_values(self) -> list[str]:
        """Return comma- or line-separated platform values."""
        return [
            value.strip()
            for line in self.platforms.splitlines()
            for value in line.split(",")
            if value.strip()
        ]

    @property
    def format_name(self) -> str | None:
        """Return the selected exporter name, if any."""
        return self.format.strip() or None

    def resolve_request(self) -> ResolveRequest:
        """Return the browser form as a normal resolve request."""
        return ResolveRequest(
            specs=self.spec_values,
            channels=self.channel_values,
            platforms=self.platform_values,
            file=self.file or None,
            filename=self.filename.strip() or None,
        )


@dataclass
class TranscodeRequest:
    """JSON body for ``POST /transcode``."""

    file: str | None = None
    filename: str | None = None
    platforms: list[str] | None = None
    specs: list[str] | None = None
    channels: list[str] | None = None


class ParseRequest(msgspec.Struct, forbid_unknown_fields=True):
    """JSON body for ``POST /parse``."""

    file: str
    filename: str | None = None


class ParseResult(msgspec.Struct):
    """Specs and channels parsed from an input file."""

    specs: list[str]
    channels: list[str]


@dataclass
class ResolveInput:
    """One parsed request ready for a solve."""

    specs: list[str]
    channels: list[str]
    platforms: list[str]

    @classmethod
    async def from_request(
        cls,
        request: Request,
        data: ResolveRequest,
        default_platforms: list[str] | None = None,
    ) -> ResolveInput | Response:
        """Parse one request and select its solve inputs."""
        input_specs = list(data.specs or [])
        channels = list(data.channels or [])
        platforms = list(data.platforms or [])
        parsed_file: ParsedInputFile | None = None

        if data.file is not None:
            parsed = await parse_input_for_request(
                request,
                data.file,
                data.filename,
                platforms or default_platforms,
            )
            if isinstance(parsed, Response):
                return parsed
            parsed_file = parsed
            if parsed_file.is_lockfile and not platforms and default_platforms is None:
                platforms = list(parsed_file.available_platforms)
            input_specs.extend(parsed_file.specs)
            if not channels:
                channels = parsed_file.channels

        if not platforms:
            platforms = list(default_platforms or [NATIVE_SUBDIR])
        if not channels:
            channels = list(DEFAULT_CHANNELS)
        if cap_error := validate_caps(input_specs, channels, platforms):
            return cap_error

        if not input_specs:
            if parsed_file and parsed_file.is_lockfile:
                if set(platforms).issubset(parsed_file.available_platforms):
                    message = (
                        "Lockfile package records cannot be loaded from HTTP input. "
                        "Provide specs to solve."
                    )
                else:
                    message = (
                        "Lockfile input cannot be solved for the requested "
                        "platforms. Provide specs to solve."
                    )
                return Response(
                    ErrorResponse(error=message),
                    status_code=HTTP_400_BAD_REQUEST,
                )
            return Response(
                ErrorResponse(error="Provide specs or file content"),
                status_code=HTTP_400_BAD_REQUEST,
            )
        return cls(
            specs=input_specs,
            channels=channels,
            platforms=platforms,
        )

    async def results(self, request: Request) -> list[SolveResult] | Response:
        """Return native solve results."""
        payload = await run_solve(request, self.specs, self.channels, self.platforms)
        if isinstance(payload, Response):
            return payload
        body, _ = payload
        return msgspec.json.decode(body, type=list[SolveResult])

    async def cached_response(
        self,
        request: Request,
        format_name: str | None = None,
    ) -> Response:
        """Return this solve through the shared HTTP result cache."""
        return await run_cached_solve(
            request,
            self.specs,
            self.channels,
            self.platforms,
            format_name=format_name,
        )


class DiffRequest(
    msgspec.Struct,
    rename={"from_": "from"},
    forbid_unknown_fields=True,
):
    """JSON body for ``POST /diff``."""

    from_: ResolveRequest
    to: ResolveRequest
    platforms: list[str] | None = None


class DiffResponse(msgspec.Struct):
    """Resolved package diffs keyed by platform."""

    platforms: list[str]
    diff: dict[str, PlatformDiff]


class ValidationErrorResponse(msgspec.Struct, omit_defaults=True):
    """Litestar's request validation error payload."""

    status_code: int
    detail: str
    extra: list[dict[str, str]] | None = None


class ExplainRequest(msgspec.Struct, forbid_unknown_fields=True):
    """JSON body for ``POST /explain``."""

    package: str
    specs: list[str] | None = None
    file: str | None = None
    filename: str | None = None
    channels: list[str] | None = None
    platforms: list[str] | None = None

    def resolve_request(self) -> ResolveRequest:
        """Return this explanation request as a normal resolve request."""
        return ResolveRequest(
            specs=self.specs,
            file=self.file,
            filename=self.filename,
            channels=self.channels,
            platforms=self.platforms,
        )


class RepairRequest(msgspec.Struct, forbid_unknown_fields=True):
    """JSON body for ``POST /repair``."""

    specs: list[str]
    channels: list[str] | None = None
    platforms: list[str] | None = None


class RepairChange(msgspec.Struct, rename={"from_": "from"}):
    """One user-supplied spec changed by a repair suggestion."""

    from_: str
    to: str
    strategy: Literal[
        "relax_exact_pin",
        "drop_upper_bound",
        "drop_lower_bound",
    ]


class RepairSuggestion(msgspec.Struct):
    """A candidate that solved on every requested platform."""

    changes: list[RepairChange]
    solve_attempts: int
    platforms: list[str]


class RepairDiagnosis(msgspec.Struct):
    """Solver error from the original request."""

    kind: Literal["solver_conflict"]
    summary: str


class RepairResult(msgspec.Struct):
    """Response body for ``POST /repair``."""

    feasible: bool
    diagnosis: RepairDiagnosis | None
    suggestions: list[RepairSuggestion]
    completion_reason: Literal[
        "feasible",
        "exhausted",
        "suggestion_limit",
        "attempt_limit",
        "time_limit",
    ]


@dataclass
class RepairSearch:
    """Evaluate single-spec repair candidates within request limits."""

    specs: list[str]
    match_specs: list[MatchSpec]
    channels: list[str]
    platforms: list[str]
    max_suggestions: int
    max_attempts: int
    deadline: float
    attempts: int = 0
    suggestions: list[RepairSuggestion] = field(default_factory=list)

    def candidates(self) -> Iterator[tuple[list[str], RepairChange]]:
        """Yield deterministic single-spec relaxation candidates."""
        for position, spec in enumerate(self.match_specs):
            if spec.get_raw_value("url") or spec.get_raw_value("fn"):
                continue
            replacements = []
            if spec.get_exact_value("version") is not None:
                replacements.append(("*", "relax_exact_pin"))
            else:
                version = spec.get_raw_value("version")
                bounds = version.split(",") if version else []
                lower = [bound for bound in bounds if bound.startswith((">=", ">"))]
                upper = [bound for bound in bounds if bound.startswith(("<=", "<"))]
                if len(bounds) == 2 and len(lower) == len(upper) == 1:
                    replacements.extend(
                        [
                            (lower[0], "drop_upper_bound"),
                            (upper[0], "drop_lower_bound"),
                        ]
                    )
            for version, strategy in replacements:
                candidate_specs = list(self.specs)
                candidate_specs[position] = str(MatchSpec(spec, version=version))
                yield (
                    candidate_specs,
                    RepairChange(
                        from_=self.specs[position],
                        to=candidate_specs[position],
                        strategy=strategy,
                    ),
                )

    async def evaluate(
        self, request: Request, specs: list[str]
    ) -> Response | list[SolveResult] | None:
        """Solve a candidate within the remaining repair budget."""
        timeout_s = min(SOLVE_TIMEOUT_S, self.deadline - time.monotonic())
        if timeout_s <= 0:
            return None
        payload = await run_solve(
            request,
            specs,
            self.channels,
            self.platforms,
            timeout_s=timeout_s,
            captured_errors=SAFE_ERROR_TYPES,
        )
        if isinstance(payload, Response):
            if payload.status_code == HTTP_504_GATEWAY_TIMEOUT:
                return None
            return payload
        body, _ = payload
        return msgspec.json.decode(body, type=list[SolveResult])

    def result(
        self,
        diagnosis: RepairDiagnosis,
        completion_reason: Literal[
            "exhausted",
            "suggestion_limit",
            "attempt_limit",
            "time_limit",
        ],
    ) -> RepairResult:
        """Build the repair result from the completed search state."""
        return RepairResult(
            feasible=False,
            diagnosis=diagnosis,
            suggestions=self.suggestions,
            completion_reason=completion_reason,
        )

    async def run(self, request: Request) -> RepairResult | Response:
        """Evaluate the original request and then its candidates."""
        original = await self.evaluate(request, self.specs)
        if isinstance(original, Response):
            return original
        if original is None:
            return Response(
                ErrorResponse(error="Repair exceeded its time budget"),
                status_code=HTTP_504_GATEWAY_TIMEOUT,
            )
        if all(result.error is None for result in original):
            return RepairResult(
                feasible=True,
                diagnosis=None,
                suggestions=[],
                completion_reason="feasible",
            )

        diagnosis = RepairDiagnosis(
            kind="solver_conflict",
            summary=next(
                result.error for result in original if result.error is not None
            ),
        )
        for candidate_specs, change in self.candidates():
            if len(self.suggestions) == self.max_suggestions:
                return self.result(diagnosis, "suggestion_limit")
            if self.attempts == self.max_attempts:
                return self.result(diagnosis, "attempt_limit")
            self.attempts += 1
            results = await self.evaluate(request, candidate_specs)
            if isinstance(results, Response):
                return results
            if results is None:
                return self.result(diagnosis, "time_limit")
            if all(result.error is None for result in results):
                self.suggestions.append(
                    RepairSuggestion(
                        changes=[change],
                        solve_attempts=self.attempts,
                        platforms=self.platforms,
                    )
                )
        return self.result(diagnosis, "exhausted")


def validate_caps(
    specs: list[str],
    channels: list[str],
    platforms: list[str],
    validate_channel_allowlist: bool = True,
) -> Response | None:
    """Return a 400 response if per-request caps are exceeded, else None."""
    if len(specs) > MAX_SPECS:
        return Response(
            {
                "error": (
                    f"Too many specs: {len(specs)} > {MAX_SPECS} "
                    f"(CONDA_PRESTO_MAX_SPECS)"
                )
            },
            status_code=HTTP_400_BAD_REQUEST,
        )
    if len(channels) > MAX_CHANNELS:
        return Response(
            {
                "error": (
                    f"Too many channels: {len(channels)} > {MAX_CHANNELS} "
                    f"(CONDA_PRESTO_MAX_CHANNELS)"
                )
            },
            status_code=HTTP_400_BAD_REQUEST,
        )
    if len(platforms) > MAX_PLATFORMS:
        return Response(
            {
                "error": (
                    f"Too many platforms: {len(platforms)} > "
                    f"{MAX_PLATFORMS} (CONDA_PRESTO_MAX_PLATFORMS)"
                )
            },
            status_code=HTTP_400_BAD_REQUEST,
        )
    invalid_platforms = sorted(set(platforms) - set(KNOWN_SUBDIRS))
    if invalid_platforms:
        return Response(
            {
                "error": f"Unsupported platform(s): {', '.join(invalid_platforms)}",
                "supported": sorted(KNOWN_SUBDIRS),
            },
            status_code=HTTP_400_BAD_REQUEST,
        )
    if validate_channel_allowlist:
        if channel_error := validate_channels(channels):
            return channel_error
    return None


def resolved_channel_urls(channel: str) -> set[str]:
    """Return credential-free channel base URLs for allowlist comparison."""
    return {
        url.removesuffix("/noarch")
        for url in Channel(channel).urls(
            with_credentials=False,
            subdirs=("noarch",),
        )
    }


def validate_channels(channels: list[str]) -> Response | None:
    """Return a 400 response when channels are outside the server allowlist."""
    try:
        allowed = {
            url
            for channel in CHANNEL_ALLOWLIST
            if channel != "*"
            for url in resolved_channel_urls(channel)
        }
    except Exception:
        log.exception("Invalid channel allowlist configuration")
        return Response(
            {"error": "Invalid server channel configuration"},
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )

    invalid = []
    for channel in channels:
        try:
            resolved = resolved_channel_urls(channel)
        except Exception:
            invalid.append(channel)
            continue
        wildcard_http = "*" in CHANNEL_ALLOWLIST and all(
            urlsplit(url).scheme in {"http", "https"} for url in resolved
        )
        if (
            not channel.strip()
            or not resolved
            or (not wildcard_http and not resolved.issubset(allowed))
        ):
            invalid.append(channel)
    if invalid:
        return Response(
            {"error": "Unsupported channel(s)"},
            status_code=HTTP_400_BAD_REQUEST,
        )
    return None


async def parse_input_for_request(
    request: Request,
    content: str,
    filename: str | None,
    target_platforms: list[str] | None = None,
    *,
    transcode_format: str | None = None,
) -> ParsedInputFile | Response:
    """Parse input off the event loop with a bounded wall-clock time."""
    capacity = request.app.state.solver_limiter
    deadline = time.monotonic() + PARSE_TIMEOUT_S
    try:
        with anyio.fail_after(PARSE_TIMEOUT_S):
            return await anyio.to_thread.run_sync(
                partial(
                    ParsedInputFile.from_content_until,
                    content,
                    filename,
                    target_platforms,
                    deadline,
                    transcode_format=transcode_format,
                ),
                limiter=capacity.arrive() if capacity is not None else None,
                abandon_on_cancel=False,
            )
    except TimeoutError:
        log.warning("Parse timeout after %ss", PARSE_TIMEOUT_S)
        return Response(
            {"error": f"Parse exceeded {PARSE_TIMEOUT_S}s timeout"},
            status_code=HTTP_504_GATEWAY_TIMEOUT,
        )
    except (CondaError, ValueError) as exc:
        return Response({"error": str(exc)}, status_code=HTTP_400_BAD_REQUEST)


async def run_solve(
    request: Request,
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
    format_name: str | None = None,
    timeout_s: float | None = None,
    captured_errors: tuple[type[Exception], ...] = (Exception,),
) -> Response | tuple[bytes, str]:
    """Shared solve runner: threadpool + timeout + error sanitization.

    When *format_name* is ``None``, runs the native path
    (``solve`` → ``list[SolveResult]`` as JSON).  When set, runs the
    exporter path (``solve_environments`` → conda exporter plugin →
    string body with a format-appropriate ``Content-Type``).
    """

    timeout_s = SOLVE_TIMEOUT_S if timeout_s is None else timeout_s
    deadline = time.monotonic() + timeout_s
    try:
        capacity = request.app.state.solver_limiter
        limiter = capacity.arrive() if capacity is not None else None
        worker = getattr(request.app.state, "solve_worker", None)
        if worker is not None:
            with anyio.fail_after(timeout_s):
                result = await anyio.to_thread.run_sync(
                    worker.solve,
                    channels,
                    specs,
                    platforms,
                    format_name,
                    deadline,
                    limiter=limiter,
                    abandon_on_cancel=True,
                )
        elif limiter is None:
            with anyio.fail_after(timeout_s):
                result = await anyio.to_thread.run_sync(
                    run_solve_work,
                    channels,
                    specs,
                    platforms,
                    format_name,
                    captured_errors,
                    abandon_on_cancel=True,
                )
        else:
            with anyio.fail_after(timeout_s):
                result = await anyio.to_thread.run_sync(
                    run_solve_in_process,
                    channels,
                    specs,
                    platforms,
                    format_name,
                    deadline,
                    captured_errors,
                    abandon_on_cancel=False,
                    limiter=limiter,
                )
    except TimeoutError:
        log.warning(
            "Solve timeout after %ss (specs=%d platforms=%s format=%s)",
            timeout_s,
            len(specs),
            platforms,
            format_name,
        )
        return Response(
            {"error": f"Solve exceeded {timeout_s}s timeout"},
            status_code=HTTP_504_GATEWAY_TIMEOUT,
        )
    except UnknownFormatError as exc:
        return Response(
            {"error": str(exc), "available_formats": exc.available},
            status_code=HTTP_400_BAD_REQUEST,
        )
    except Exception:
        log.exception("Solve failed")
        return Response(
            {"error": "Internal solver error"},
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if format_name is None:
        return msgspec.json.encode(result), "application/json"
    body, media_type = result
    if isinstance(body, str):
        body = body.encode()
    return body, media_type


def run_solve_work(
    channels: list[str],
    specs: list[str],
    platforms: list[str] | None,
    format_name: str | None,
    captured_errors: tuple[type[Exception], ...] = (Exception,),
) -> list | tuple[str, str]:
    """Run the blocking solve/export path in a worker."""
    if format_name is None:
        return solve(
            channels,
            specs,
            platforms,
            captured_errors=captured_errors,
        )
    envs = solve_environments(channels, specs, platforms)
    return OutputFormat.named(format_name).render(envs)


def run_solve_in_process(
    channels: list[str],
    specs: list[str],
    platforms: list[str] | None,
    format_name: str | None,
    deadline: float,
    captured_errors: tuple[type[Exception], ...] = (Exception,),
) -> list | tuple[str, str]:
    """Run solve work in a child process until the request deadline."""
    if deadline <= time.monotonic():
        raise TimeoutError
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=solve_process_entrypoint,
        args=(
            sender,
            channels,
            specs,
            platforms,
            format_name,
            captured_errors,
        ),
    )
    process.start()
    sender.close()

    try:
        if not receiver.poll(max(0.0, deadline - time.monotonic())):
            raise TimeoutError
        status, payload = receiver.recv()
    except EOFError as exc:
        raise RuntimeError(f"Solve worker exited with code {process.exitcode}") from exc
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(max(0.0, min(5.0, deadline - time.monotonic())))
        if process.is_alive():
            process.kill()
        process.join(5)
        if process.is_alive():
            raise RuntimeError("Solve worker did not exit after kill")

    if status == "ok":
        return payload
    if status == "unknown-format":
        raise UnknownFormatError(payload["format_name"], payload["available"])
    raise RuntimeError("Solve worker failed")


def solve_process_entrypoint(
    sender,
    channels: list[str],
    specs: list[str],
    platforms: list[str] | None,
    format_name: str | None,
    captured_errors: tuple[type[Exception], ...] = (Exception,),
) -> None:
    """Send a solve result from an isolated process."""
    CredentialRedactionFilter.install()
    try:
        sender.send(
            (
                "ok",
                run_solve_work(
                    channels,
                    specs,
                    platforms,
                    format_name,
                    captured_errors,
                ),
            )
        )
    except UnknownFormatError as exc:
        sender.send(
            (
                "unknown-format",
                {"format_name": exc.format_name, "available": exc.available},
            )
        )
    except Exception:
        log.exception("Isolated solve failed")
        sender.send(("error", None))
    finally:
        sender.close()


async def run_cached_solve(
    request: Request,
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
    format_name: str | None = None,
) -> Response:
    """Run a solve through the content-addressed result cache."""
    cache: ResultCache = request.app.state.result_cache
    retain_result = not (
        cache.channels_have_credentials(channels) or cache.specs_have_credentials(specs)
    )
    exporter_identity: ExporterCacheIdentity | None = None
    if format_name is not None:
        try:
            output_format = OutputFormat.named(format_name)
        except UnknownFormatError as exc:
            return Response(
                {"error": str(exc), "available_formats": exc.available},
                status_code=HTTP_400_BAD_REQUEST,
                headers={"Cache-Control": "no-store"},
            )
        exporter_identity = output_format.cache_identity()
        retain_result = retain_result and exporter_identity is not None
    resolved_platforms = list(platforms or [NATIVE_SUBDIR])
    capacity = request.app.state.solver_limiter
    deadline = time.monotonic() + SOLVE_TIMEOUT_S
    try:
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            initial_repodata, solve_context = await anyio.to_thread.run_sync(
                cache.capture_state,
                channels,
                resolved_platforms,
                limiter=capacity.arrive() if capacity is not None else None,
                abandon_on_cancel=False,
            )
            if time.monotonic() >= deadline:
                raise TimeoutError
            digest = cache.key_for(
                specs,
                channels,
                platforms,
                format_name,
                initial_repodata,
                solve_context=solve_context,
                exporter_identity=exporter_identity,
            )
            key = cache.resolve_key(digest)
            location = f"/r/{digest}"
            if retain_result and not initial_repodata.stale:
                cached_response = await cache.get_response(key, location=location)
                if cached_response is not None:
                    current_repodata = await anyio.to_thread.run_sync(
                        RepodataSnapshot.capture,
                        channels,
                        resolved_platforms,
                        limiter=capacity.arrive() if capacity is not None else None,
                        abandon_on_cancel=False,
                    )
                    if (
                        not current_repodata.stale
                        and current_repodata.records == initial_repodata.records
                    ):
                        if time.monotonic() >= deadline:
                            raise TimeoutError
                        return cached_response
                    initial_repodata = current_repodata

            remaining_s = deadline - time.monotonic()
            if remaining_s <= 0:
                raise TimeoutError
            payload = await run_solve(
                request,
                specs,
                channels,
                platforms,
                format_name=format_name,
                timeout_s=remaining_s,
            )
            if isinstance(payload, Response):
                return payload

            # Recompute so refreshed repodata and the worker's index agree.
            repodata = await anyio.to_thread.run_sync(
                RepodataSnapshot.capture,
                channels,
                resolved_platforms,
                limiter=capacity.arrive() if capacity is not None else None,
                abandon_on_cancel=False,
            )
            if time.monotonic() >= deadline:
                raise TimeoutError
            digest = cache.key_for(
                specs,
                channels,
                platforms,
                format_name,
                repodata,
                solve_context=solve_context,
                exporter_identity=exporter_identity,
            )
            key = cache.resolve_key(digest)
            body, media_type = payload
            return await cache.remember(
                key,
                body,
                media_type,
                location=f"/r/{digest}",
                retain=retain_result
                and repodata.records == initial_repodata.records
                and repodata.is_cacheable_after(initial_repodata),
            )
    except TimeoutError:
        log.warning("Cached solve timeout after %ss", SOLVE_TIMEOUT_S)
        return Response(
            {"error": f"Solve exceeded {SOLVE_TIMEOUT_S}s timeout"},
            status_code=HTTP_504_GATEWAY_TIMEOUT,
            headers={"Cache-Control": "no-store"},
        )


def transcode_rejection(
    parsed: ParsedInputFile | None,
    output_format: OutputFormat | None,
    target_platforms: list[str],
    has_extra_specs: bool,
    has_channel_override: bool,
) -> Response:
    """Return a structured transcode rejection response."""
    reasons: list[str] = []
    if parsed is None:
        reasons.append("no file input was provided")
    elif not parsed.is_lockfile:
        reasons.append("input file is not a lockfile")
    else:
        missing = sorted(set(target_platforms) - set(parsed.available_platforms))
        if missing:
            reasons.append(
                "requested platforms not present in lockfile: " + ", ".join(missing)
            )
        elif (
            output_format is not None
            and output_format.is_lockfile
            and not has_extra_specs
            and not has_channel_override
        ):
            if parsed.transcoded_content is None:
                reasons.append(
                    "input lockfile format does not support no-download transcoding"
                )
    if output_format is None:
        reasons.append("no output format was requested")
    elif not output_format.is_lockfile:
        reasons.append("output format is not a lockfile")
    if has_extra_specs:
        reasons.append("additional specs require solving")
    if has_channel_override:
        reasons.append("channel overrides require solving")
    return Response(
        {
            "error": "Request cannot be transcoded",
            "reasons": reasons,
        },
        status_code=HTTP_400_BAD_REQUEST,
    )


@get("/", include_in_schema=False)
async def workbench() -> Template:
    """Serve the first-party conda-presto workbench."""
    current_version = pkg_version("conda-presto")
    return Template(
        template_name="workbench.html",
        context={
            "channels": "\n".join(DEFAULT_CHANNELS),
            "formats": OutputFormat.available(),
            "platforms": NATIVE_SUBDIR,
            "version": current_version,
        },
        headers=WORKBENCH_HEADERS,
    )


@get(
    "/openapi.json",
    media_type="application/vnd.oai.openapi+json",
    include_in_schema=False,
)
async def openapi_json(request: Request) -> dict[str, object]:
    """Serve the generated OpenAPI document at its stable public path."""
    return request.app.openapi_schema.to_schema()


@post("/ui/preflight", status_code=200, include_in_schema=False)
async def workbench_preflight(
    request: Request,
    data: URLEncodedBody[WorkbenchForm],
    hx_request: Annotated[
        Literal["true"],
        HeaderParameter(name="HX-Request"),
    ],
) -> Template:
    """Render preflight findings for the browser workbench."""
    started = time.perf_counter()
    result = await data.resolve_request().preflight(request)
    duration_ms = round((time.perf_counter() - started) * 1_000)
    if isinstance(result, Response):
        return Template(
            template_name="fragments/error.html",
            context={
                "error": result.content,
                "operation": "Preflight",
                "status_code": result.status_code,
            },
            headers=WORKBENCH_HEADERS,
            status_code=result.status_code,
        )
    return Template(
        template_name="fragments/preflight.html",
        context={"duration_ms": duration_ms, "result": result},
        headers=WORKBENCH_HEADERS,
    )


@post("/ui/resolve", status_code=200, include_in_schema=False)
async def workbench_resolve(
    request: Request,
    data: URLEncodedBody[WorkbenchForm],
    hx_request: Annotated[
        Literal["true"],
        HeaderParameter(name="HX-Request"),
    ],
) -> Template:
    """Render a cached solve or exporter result for the browser workbench."""
    started = time.perf_counter()
    source = await ResolveInput.from_request(
        request,
        data.resolve_request(),
        [NATIVE_SUBDIR],
    )
    if isinstance(source, Response):
        return Template(
            template_name="fragments/error.html",
            context={
                "error": source.content,
                "operation": "Resolve",
                "status_code": source.status_code,
            },
            headers=WORKBENCH_HEADERS,
            status_code=source.status_code,
        )

    response = await source.cached_response(request, data.format_name)
    duration_ms = round((time.perf_counter() - started) * 1_000)
    if (response.status_code or 200) >= HTTP_400_BAD_REQUEST:
        return Template(
            template_name="fragments/error.html",
            context={
                "error": response.content,
                "operation": "Resolve",
                "status_code": response.status_code,
            },
            headers=WORKBENCH_HEADERS,
            status_code=response.status_code,
        )

    location = response.headers.get("Location")
    if location is not None:
        digest = location.removeprefix("/r/")
        if (
            location != f"/r/{digest}"
            or len(digest) != 64
            or not all(character in "0123456789abcdef" for character in digest)
        ):
            location = None
    if data.format_name is not None:
        try:
            output = (
                response.content.decode("utf-8")
                if isinstance(response.content, bytes)
                else str(response.content)
            )
        except UnicodeDecodeError:
            log.exception("Unable to decode workbench exporter output")
            return Template(
                template_name="fragments/error.html",
                context={
                    "error": {"error": "Internal exporter output error"},
                    "operation": "Resolve",
                    "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
                },
                headers=WORKBENCH_HEADERS,
                status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Template(
            template_name="fragments/resolve.html",
            context={
                "duration_ms": duration_ms,
                "format_name": data.format_name,
                "location": location,
                "output": output,
                "results": None,
                "failed_platforms": None,
                "total_packages": None,
            },
            headers=WORKBENCH_HEADERS,
        )

    try:
        results = msgspec.json.decode(response.content, type=list[SolveResult])
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError):
        log.exception("Unable to decode workbench solve result")
        return Template(
            template_name="fragments/error.html",
            context={
                "error": {"error": "Internal solver output error"},
                "operation": "Resolve",
                "status_code": HTTP_500_INTERNAL_SERVER_ERROR,
            },
            headers=WORKBENCH_HEADERS,
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return Template(
        template_name="fragments/resolve.html",
        context={
            "duration_ms": duration_ms,
            "failed_platforms": sum(result.error is not None for result in results),
            "format_name": None,
            "location": location,
            "output": None,
            "results": results,
            "total_packages": sum(len(result.packages) for result in results),
        },
        headers=WORKBENCH_HEADERS,
    )


@get("/resolve")
async def resolve_get(
    request: Request,
    spec: FromQuery[list[str] | None] = None,
    channel: FromQuery[list[str] | None] = None,
    platform: FromQuery[list[str] | None] = None,
    format: FromQuery[str | None] = None,
) -> Response:
    """Resolve package specs via query params.

    Pass ``?format=<name>`` to route the response through conda's
    exporter plugin registry (e.g. ``explicit``, ``environment-yaml``,
    ``conda-lock-v1``).
    """
    specs = spec or []
    channels = channel or []
    platforms = platform or []

    if not specs:
        return Response(
            {"error": "Provide specs or file content"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    if cap_error := validate_caps(specs, channels, platforms):
        return cap_error

    return await run_cached_solve(
        request, specs, channels, platforms or None, format_name=format
    )


@post(
    "/resolve",
    status_code=200,
)
async def resolve_post(
    request: Request,
    spec: FromQuery[list[str] | None] = None,
    channel: FromQuery[list[str] | None] = None,
    platform: FromQuery[list[str] | None] = None,
    format: FromQuery[str | None] = None,
    filename: FromQuery[str | None] = None,
) -> Response:
    """Resolve package specs and/or an input file via POST body.

    Dispatch on ``Content-Type``:

    * ``application/json`` (or missing): body is a :class:`ResolveRequest`
      envelope.  Body fields override query params by presence — an
      explicit empty array in the body overrides the corresponding
      query param; an omitted field falls through.
    * ``application/yaml`` / ``application/x-yaml`` / ``text/yaml`` /
      ``application/toml`` / ``text/plain``: the body *is* the raw
      input file content (e.g. an ``environment.yml``).  Specs,
      channels, and platforms come from query params only.  The
      parser is picked from ``Content-Type``; pass ``?filename=`` to
      override (e.g. ``?filename=pixi.lock`` to force the lockfile
      parser when Content-Type is a generic YAML).

    Pass ``?format=<name>`` on either dispatch to route the response
    through conda's exporter plugin registry.  ``format`` is
    query-only; it is not read from the JSON body.
    """
    data = await ResolveRequest.from_http(request, spec, channel, platform, filename)
    if isinstance(data, Response):
        return data

    file_content = data.file
    file_name = data.filename
    specs = data.specs or []
    channels = data.channels or []
    platforms = data.platforms or []
    parsed_file: ParsedInputFile | None = None

    if file_content is not None:
        parsed = await parse_input_for_request(
            request, file_content, file_name, platforms or [NATIVE_SUBDIR]
        )
        if isinstance(parsed, Response):
            return parsed
        parsed_file = parsed

        specs = list(specs) + parsed_file.specs
        if not channels:
            channels = parsed_file.channels

    if not specs:
        if parsed_file and parsed_file.is_lockfile:
            if set(platforms or [NATIVE_SUBDIR]).issubset(
                parsed_file.available_platforms
            ):
                message = (
                    "Lockfile package records cannot be loaded from HTTP input. "
                    "Provide specs to solve."
                )
            else:
                message = (
                    "Lockfile input cannot be solved for the requested platforms. "
                    "Request a lockfile output for a platform present in the "
                    "lockfile or provide specs to solve."
                )
            return Response(
                {"error": message},
                status_code=HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"error": "Provide specs or file content"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    if cap_error := validate_caps(specs, channels, platforms):
        return cap_error

    return await run_cached_solve(
        request, specs, channels, platforms or None, format_name=format
    )


@post(
    "/preflight",
    status_code=200,
    responses={
        200: ResponseSpec(
            data_container=PreflightResult,
            description="Preflight findings",
        ),
        HTTP_400_BAD_REQUEST: ResponseSpec(
            data_container=ErrorResponse,
            description="Input error",
        ),
        HTTP_504_GATEWAY_TIMEOUT: ResponseSpec(
            data_container=ErrorResponse,
            description="Input parsing timed out",
        ),
    },
)
async def preflight_post(
    request: Request,
    spec: FromQuery[list[str] | None] = None,
    channel: FromQuery[list[str] | None] = None,
    platform: FromQuery[list[str] | None] = None,
    filename: FromQuery[str | None] = None,
) -> Response:
    """Validate a resolve request without a solve or channel access."""
    data = await ResolveRequest.from_http(request, spec, channel, platform, filename)
    if isinstance(data, Response):
        return data

    result = await data.preflight(request)
    return result if isinstance(result, Response) else Response(result)


@post(
    "/repair",
    status_code=200,
    responses={
        200: ResponseSpec(
            data_container=RepairResult,
            description="Single-spec repair suggestions",
        ),
        HTTP_400_BAD_REQUEST: ResponseSpec(
            data_container=ErrorResponse | ValidationErrorResponse,
            description="Input or request validation error",
        ),
        HTTP_500_INTERNAL_SERVER_ERROR: ResponseSpec(
            data_container=ErrorResponse,
            description="Internal solver error",
        ),
        HTTP_504_GATEWAY_TIMEOUT: ResponseSpec(
            data_container=ErrorResponse,
            description="Repair search time budget exceeded",
        ),
    },
)
async def repair_post(
    request: Request,
    data: RepairRequest,
    max_suggestions: Annotated[int | None, QueryParameter(ge=1)] = None,
    max_attempts: Annotated[int | None, QueryParameter(ge=1)] = None,
    time_budget_ms: Annotated[int | None, QueryParameter(ge=1)] = None,
) -> Response:
    """Return relaxations that solve on every requested platform."""
    if not data.specs:
        return Response(
            ErrorResponse(error="Provide at least one spec"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    channels = list(data.channels or DEFAULT_CHANNELS)
    platforms = list(data.platforms or [NATIVE_SUBDIR])
    if cap_error := validate_caps(data.specs, channels, platforms):
        return cap_error
    try:
        match_specs = [MatchSpec(spec) for spec in data.specs]
    except (CondaError, ValueError) as exc:
        return Response(ErrorResponse(error=str(exc)), status_code=HTTP_400_BAD_REQUEST)

    search = RepairSearch(
        specs=data.specs,
        match_specs=match_specs,
        channels=channels,
        platforms=platforms,
        max_suggestions=min(
            max_suggestions or MAX_REPAIR_SUGGESTIONS, MAX_REPAIR_SUGGESTIONS
        ),
        max_attempts=min(max_attempts or MAX_REPAIR_ATTEMPTS, MAX_REPAIR_ATTEMPTS),
        deadline=time.monotonic()
        + min(time_budget_ms or MAX_REPAIR_TIME_BUDGET_MS, MAX_REPAIR_TIME_BUDGET_MS)
        / 1_000,
    )
    result = await search.run(request)
    if isinstance(result, Response):
        return result
    return Response(result)


@post(
    "/diff",
    status_code=200,
    responses={
        200: ResponseSpec(
            data_container=DiffResponse,
            description="Resolved package differences",
        ),
        HTTP_400_BAD_REQUEST: ResponseSpec(
            data_container=ErrorResponse | ValidationErrorResponse,
            description="Input or request validation error",
        ),
        HTTP_422_UNPROCESSABLE_ENTITY: ResponseSpec(
            data_container=ErrorResponse,
            description="Unsatisfiable environment",
        ),
        HTTP_500_INTERNAL_SERVER_ERROR: ResponseSpec(
            data_container=ErrorResponse,
            description="Internal solver error",
        ),
        HTTP_504_GATEWAY_TIMEOUT: ResponseSpec(
            data_container=ErrorResponse,
            description="Solve or parsing timeout",
        ),
    },
)
async def diff_post(request: Request, data: DiffRequest) -> Response:
    """Compare the packages selected by two resolve inputs."""
    if data.platforms is not None:
        before_request = replace(data.from_, platforms=data.platforms)
        after_request = replace(data.to, platforms=data.platforms)
    elif data.from_.platforms is not None and data.to.platforms is not None:
        platforms = [
            platform
            for platform in data.from_.platforms
            if platform in data.to.platforms
        ]
        if not platforms:
            return Response(
                ErrorResponse(error="The two inputs have no platforms in common"),
                status_code=HTTP_400_BAD_REQUEST,
            )
        before_request = replace(data.from_, platforms=platforms)
        after_request = replace(data.to, platforms=platforms)
    elif data.from_.platforms is not None:
        before_request = data.from_
        after_request = replace(data.to, platforms=data.from_.platforms)
    elif data.to.platforms is not None:
        before_request = replace(data.from_, platforms=data.to.platforms)
        after_request = data.to
    else:
        before_request = data.from_
        after_request = data.to

    before = await ResolveInput.from_request(request, before_request)
    if isinstance(before, Response):
        return before
    after = await ResolveInput.from_request(request, after_request)
    if isinstance(after, Response):
        return after

    before_results = await before.results(request)
    if isinstance(before_results, Response):
        return before_results
    after_results = await after.results(request)
    if isinstance(after_results, Response):
        return after_results
    for result in [*before_results, *after_results]:
        if result.error is not None:
            return Response(
                ErrorResponse(error=result.error, platform=result.platform),
                status_code=HTTP_422_UNPROCESSABLE_ENTITY,
            )

    after_by_platform = {result.platform: result for result in after_results}
    platforms = [
        result.platform
        for result in before_results
        if result.platform in after_by_platform
    ]
    if not platforms:
        return Response(
            ErrorResponse(error="The two inputs have no platforms in common"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    before_by_platform = {result.platform: result for result in before_results}
    return Response(
        DiffResponse(
            platforms=platforms,
            diff={
                platform: before_by_platform[platform].diff(
                    after_by_platform[platform],
                )
                for platform in platforms
            },
        )
    )


@post(
    "/explain",
    status_code=200,
    responses={
        200: ResponseSpec(
            data_container=ExplainResult,
            description="Dependency chains for the selected package",
        ),
        HTTP_400_BAD_REQUEST: ResponseSpec(
            data_container=ErrorResponse | ValidationErrorResponse,
            description="Input or request validation error",
        ),
        HTTP_404_NOT_FOUND: ResponseSpec(
            data_container=ErrorResponse,
            description="Selected package is absent",
        ),
        HTTP_422_UNPROCESSABLE_ENTITY: ResponseSpec(
            data_container=ErrorResponse,
            description="Unsatisfiable environment",
        ),
        HTTP_500_INTERNAL_SERVER_ERROR: ResponseSpec(
            data_container=ErrorResponse,
            description="Internal solver error",
        ),
        HTTP_504_GATEWAY_TIMEOUT: ResponseSpec(
            data_container=ErrorResponse,
            description="Solve or parsing timeout",
        ),
    },
)
async def explain_post(request: Request, data: ExplainRequest) -> Response:
    """Explain why a package appears in a single-platform resolution."""
    if not data.package:
        return Response(
            ErrorResponse(error="Provide a package name"),
            status_code=HTTP_400_BAD_REQUEST,
        )

    source = await ResolveInput.from_request(
        request,
        data.resolve_request(),
        default_platforms=[NATIVE_SUBDIR],
    )
    if isinstance(source, Response):
        return source
    if len(source.platforms) != 1:
        return Response(
            ErrorResponse(error="/explain requires exactly one platform"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    results = await source.results(request)
    if isinstance(results, Response):
        return results
    result = results[0]
    if result.error is not None:
        return Response(
            ErrorResponse(error=result.error),
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        )
    explanation = result.explain(source.specs, data.package)
    if explanation is None:
        return Response(
            ErrorResponse(error=f"Package not found: {data.package}"),
            status_code=HTTP_404_NOT_FOUND,
        )
    return Response(explanation)


@post(
    "/transcode",
    status_code=200,
)
async def transcode_post(
    request: Request,
    spec: FromQuery[list[str] | None] = None,
    channel: FromQuery[list[str] | None] = None,
    platform: FromQuery[list[str] | None] = None,
    format: FromQuery[str | None] = None,
    filename: FromQuery[str | None] = None,
) -> Response:
    """Convert one lockfile format to another without solving."""
    content_type, _ = request.content_type

    file_content: str | None = None
    file_name: str | None = None
    platforms: list[str] = platform or []
    body_specs: list[str] = []
    body_channels: list[str] = []

    if content_type in ("", "application/json"):
        body = await request.body()
        if body:
            try:
                data = msgspec.json.decode(body, type=TranscodeRequest)
            except (msgspec.DecodeError, msgspec.ValidationError) as exc:
                return Response(
                    {"error": f"Invalid JSON body: {exc}"},
                    status_code=HTTP_400_BAD_REQUEST,
                )
        else:
            data = TranscodeRequest()

        file_content = data.file
        file_name = data.filename or filename
        platforms = data.platforms if data.platforms is not None else platforms
        body_specs = data.specs or []
        body_channels = data.channels or []
    elif content_type in RAW_CONTENT_TYPE_EXTENSIONS:
        body = await request.body()
        try:
            file_content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            return Response(
                {"error": f"Body is not valid UTF-8: {exc}"},
                status_code=HTTP_400_BAD_REQUEST,
            )
        file_name = filename or (
            f"environment{RAW_CONTENT_TYPE_EXTENSIONS[content_type]}"
        )
    else:
        return Response(
            {
                "error": (
                    f"Unsupported Content-Type {content_type!r}. "
                    "Use application/json for a TranscodeRequest envelope, "
                    "or application/yaml / application/toml / text/plain "
                    "for a raw lockfile body."
                ),
                "supported": [
                    "application/json",
                    *sorted(RAW_CONTENT_TYPE_EXTENSIONS),
                ],
            },
            status_code=HTTP_400_BAD_REQUEST,
        )

    target_platforms = platforms or [NATIVE_SUBDIR]
    if cap_error := validate_caps([], [], target_platforms):
        return cap_error

    has_extra_specs = bool(spec) or bool(body_specs)
    has_channel_override = bool(channel) or bool(body_channels)
    output_format = None
    if format is not None:
        try:
            output_format = OutputFormat.named(format)
        except UnknownFormatError as exc:
            return Response(
                {"error": str(exc), "available_formats": exc.available},
                status_code=HTTP_400_BAD_REQUEST,
            )
    if file_content is None:
        return transcode_rejection(
            None,
            output_format,
            target_platforms,
            has_extra_specs,
            has_channel_override,
        )

    transcode_format = (
        output_format.exporter.name
        if output_format is not None
        and output_format.is_lockfile
        and not has_extra_specs
        and not has_channel_override
        else None
    )
    parsed = await parse_input_for_request(
        request,
        file_content,
        file_name,
        target_platforms,
        transcode_format=transcode_format,
    )
    if isinstance(parsed, Response):
        return parsed
    parsed_file = parsed

    if (
        parsed_file.is_lockfile
        and output_format is not None
        and not has_extra_specs
        and not has_channel_override
        and output_format.is_lockfile
        and parsed_file.transcoded_content is not None
    ):
        return Response(
            parsed_file.transcoded_content,
            media_type=output_format.media_type,
            headers={"Cache-Control": "no-store"},
        )

    return transcode_rejection(
        parsed_file,
        output_format,
        target_platforms,
        has_extra_specs,
        has_channel_override,
    )


@get("/r/{key:str}")
async def result_get(request: Request, key: FromPath[str]) -> Response:
    """Return a stored content-addressed solve result."""
    cache: ResultCache = request.app.state.result_cache
    cached_response = await cache.get_response(
        cache.resolve_key(key),
        location=f"/r/{key}",
        immutable=True,
    )
    if cached_response is None:
        return Response(
            {"error": "result not in cache; re-POST to recompute"},
            status_code=HTTP_404_NOT_FOUND,
        )
    return cached_response


@get("/formats")
async def formats() -> dict[str, list[str]]:
    """Return the list of registered exporter format names."""
    return {"formats": OutputFormat.available()}


@get("/platforms")
async def platforms() -> dict[str, list[str]]:
    """Return the known conda platform subdirectory names."""
    return {"platforms": sorted(KNOWN_SUBDIRS)}


@get("/version")
async def version() -> dict[str, str]:
    """Return version info for conda-presto and its key dependencies."""
    versions: dict[str, str] = {
        "conda-presto": pkg_version("conda-presto"),
        "conda": pkg_version("conda"),
    }
    for pkg in ("conda-rattler-solver", "conda-lockfiles"):
        try:
            versions[pkg] = pkg_version(pkg)
        except Exception:
            pass
    return versions


@post(
    "/parse",
    status_code=200,
    responses={
        200: ResponseSpec(
            data_container=ParseResult,
            description="Specs and channels extracted from an input file",
        ),
        HTTP_400_BAD_REQUEST: ResponseSpec(
            data_container=ErrorResponse | ValidationErrorResponse,
            description="Input or request validation error",
        ),
        HTTP_504_GATEWAY_TIMEOUT: ResponseSpec(
            data_container=ErrorResponse,
            description="Input parsing timed out",
        ),
    },
)
async def parse(request: Request, data: ParseRequest) -> Response:
    """Parse an input file and return its specs and channels."""
    if not data.file:
        return Response(
            ErrorResponse(error="Field 'file' is required"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    parsed = await parse_input_for_request(request, data.file, data.filename)
    if isinstance(parsed, Response):
        return parsed
    parsed_file = parsed
    if cap_error := validate_caps(parsed_file.specs, parsed_file.channels, []):
        return cap_error
    return Response(ParseResult(parsed_file.specs, parsed_file.channels))


@get(
    "/health",
    responses={
        HTTP_503_SERVICE_UNAVAILABLE: ResponseSpec(
            data_container=HealthResponse,
            description="Persistent solver worker is unavailable",
        ),
    },
)
async def health(request: Request) -> Response[HealthResponse]:
    """Return readiness for the persistent worker when one is configured."""
    worker = getattr(request.app.state, "solve_worker", None)
    if worker is not None and not worker.ready:
        await anyio.to_thread.run_sync(
            worker.recover_if_stopped,
            abandon_on_cancel=True,
        )
        return Response(
            HealthResponse(status="unavailable"),
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response(HealthResponse(status="ok"))


def require_solver_service(
    connection: ASGIConnection,
    _route_handler: BaseRouteHandler,
) -> None:
    """Restrict the private solver route before Litestar parses its body."""
    client = connection.client
    service_url = os.environ.get("CONDA_PRESTO_URL")
    expected_authority = urlsplit(service_url).netloc if service_url else ""
    if (
        os.environ.get("CONDA_BROKER_SERVICE_NAME") != PrestoSolverClient.service_name
        or not isinstance(connection, Request)
        or client is None
        or not PrestoSolverClient.is_loopback(client.host)
        or "origin" in connection.headers
        or connection.content_type[0] != RequestEncodingType.JSON
        or not expected_authority
        or connection.headers.get("host", "").casefold()
        != expected_authority.casefold()
    ):
        raise NotFoundException


@post(
    "/solver/v1",
    status_code=200,
    include_in_schema=False,
    guards=[require_solver_service],
    cache_control=CacheControlHeader(no_store=True),
)
async def solver_v1(
    request: Request,
    data: PrestoSolveRequest,
) -> Response:
    """Run the broker-only internal Presto solver protocol."""
    if cap_error := validate_caps(
        [],
        [],
        data.subdirs,
        validate_channel_allowlist=False,
    ):
        return cap_error
    if len(data.channels) > MAX_SOLVER_CHANNELS:
        return Response(
            {
                "error": (
                    f"Too many solver channels: {len(data.channels)} > "
                    f"{MAX_SOLVER_CHANNELS} "
                    "(CONDA_PRESTO_MAX_SOLVER_CHANNELS)"
                )
            },
            status_code=HTTP_400_BAD_REQUEST,
        )
    state_items = sum(
        len(values)
        for values in (
            data.specs_to_add,
            data.specs_to_remove,
            data.installed,
            data.history,
            data.pinned,
            data.virtual,
            data.aggressive_updates,
            data.always_update,
        )
    )
    if state_items > MAX_SOLVER_STATE_ITEMS:
        return Response(
            {
                "error": (
                    f"Too many solver state entries: {state_items} > "
                    f"{MAX_SOLVER_STATE_ITEMS} "
                    "(CONDA_PRESTO_MAX_SOLVER_STATE_ITEMS)"
                )
            },
            status_code=HTTP_400_BAD_REQUEST,
        )
    worker = getattr(request.app.state, "solve_worker", None)
    if worker is None:
        return Response(
            ErrorResponse(error="Internal Presto solver worker is unavailable"),
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
        )
    cache: ResultCache = request.app.state.result_cache
    service = SolverResultService(cache)
    try:
        deadline = time.monotonic() + SOLVE_TIMEOUT_S
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            result = await service.probe(data)
            if time.monotonic() >= deadline:
                raise TimeoutError
            if result is None:
                async with request.app.state.solver_limiter.arrive():
                    result = await service.resolve(data, worker, deadline)
                if time.monotonic() >= deadline:
                    raise TimeoutError
    except TimeoutError:
        return Response(
            ErrorResponse(error=f"Solve exceeded {SOLVE_TIMEOUT_S}s timeout"),
            status_code=HTTP_504_GATEWAY_TIMEOUT,
        )
    except CondaError as exc:
        return Response(
            ErrorResponse(error=str(exc)),
            status_code=HTTP_422_UNPROCESSABLE_ENTITY,
        )
    except Exception:
        log.exception("Internal Presto solver failed")
        return Response(
            ErrorResponse(error="Internal solver error"),
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if isinstance(result.result, PrestoSolveError):
        return Response(result.result, status_code=HTTP_422_UNPROCESSABLE_ENTITY)
    warm_candidates = getattr(request.app.state, "solver_warm_candidates", None)
    if warm_candidates is not None and result.should_record_for_warming:
        if warm_candidates.record(data):
            warmer = getattr(request.app.state, "solver_cache_refresher", None)
            if warmer is not None:
                warmer.stats.recorded_requests += 1
    return Response(result.result)


@asynccontextmanager
async def solver_resources_lifespan(app: Litestar) -> AsyncIterator[None]:
    """Own result storage, foreground caches, and worker processes."""
    CredentialRedactionFilter.install()
    resources = AsyncExitStack()
    app.state.solve_worker = None
    try:
        await resources.__aenter__()
        store = ResultCache.store_for_config(
            RESULT_CACHE_BACKEND,
            RESULT_CACHE_DIR,
            RESULT_CACHE_REDIS_URL,
            RESULT_CACHE_REDIS_NAMESPACE,
        )
        if store is not None:
            await resources.enter_async_context(store)

        store_operations = (
            StoreOperationCoordinator(store) if store is not None else None
        )

        app.state.solver_limiter = ForegroundCapacity(
            anyio.CapacityLimiter(MAX_CONCURRENCY)
        )
        app.state.result_cache = ResultCache(
            max_size=RESULT_CACHE_SIZE,
            max_bytes=RESULT_CACHE_MAX_MEMORY_BYTES,
            store_operations=store_operations,
        )
        if PERSISTENT_WORKER:
            app.state.solve_worker = PersistentSolveWorker(
                DEFAULT_CHANNELS,
                DEFAULT_PLATFORMS,
                restart_on_failure=(
                    os.environ.get("CONDA_BROKER_SERVICE_NAME")
                    != PrestoSolverClient.service_name
                ),
            )
            log.info(
                "Starting persistent solve worker for %d channels on %s",
                len(DEFAULT_CHANNELS),
                DEFAULT_PLATFORMS,
            )
            await anyio.to_thread.run_sync(
                app.state.solve_worker.start,
                abandon_on_cancel=True,
            )
        else:
            log.info(
                "Pre-warming repodata cache for %d channels on %s",
                len(DEFAULT_CHANNELS),
                DEFAULT_PLATFORMS,
            )
            await anyio.to_thread.run_sync(
                lambda: warmup(DEFAULT_CHANNELS, DEFAULT_PLATFORMS),
                abandon_on_cancel=True,
            )
        log.info("Repodata cache warm")
        async with AsyncExitStack() as runtime:
            if store_operations is not None:
                await runtime.enter_async_context(store_operations.lifespan())
            yield
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if app.state.solve_worker is not None:
                    await anyio.to_thread.run_sync(
                        app.state.solve_worker.shutdown,
                        abandon_on_cancel=True,
                    )
            except Exception:
                log.warning("Persistent solve worker cleanup failed")
            finally:
                try:
                    shutdown_process_pool()
                finally:
                    await resources.aclose()


@asynccontextmanager
async def solver_cache_refresher_lifespan(app: Litestar) -> AsyncIterator[None]:
    """Own cache-warming candidates and the scheduled cache warmer."""
    cache: ResultCache = app.state.result_cache
    warm_candidates = SolverWarmCandidates(
        max_size=SOLVER_CACHE_WARM_CANDIDATE_SIZE,
        persist=SOLVER_CACHE_WARM_CANDIDATE_PERSIST,
        store_operations=cache.store_operations,
    )
    await warm_candidates.load()
    service_thread_limiter = anyio.CapacityLimiter(1)
    worker_thread_limiter = anyio.CapacityLimiter(1)
    warmer = SolverCacheWarmer(
        warm_candidates=warm_candidates,
        service=SolverResultService(
            cache=cache,
            thread_limiter=service_thread_limiter,
            require_persistent=cache.store_operations is not None,
        ),
        limiter=app.state.solver_limiter,
        interval_s=SOLVER_CACHE_WARM_INTERVAL_S,
        batch_size=SOLVER_CACHE_WARM_BATCH_SIZE,
        thread_limiter=worker_thread_limiter,
    )
    app.state.solver_warm_candidates = warm_candidates
    app.state.solver_cache_refresher = warmer
    enabled = (
        os.environ.get("CONDA_BROKER_SERVICE_NAME") == PrestoSolverClient.service_name
        and PERSISTENT_WORKER
        and SOLVER_CACHE_WARM_INTERVAL_S > 0
        and SOLVER_CACHE_WARM_BATCH_SIZE > 0
        and SOLVER_CACHE_WARM_CANDIDATE_SIZE > 0
        and (cache.max_size > 0 or cache.store_operations is not None)
    )
    try:
        if enabled:
            stop = anyio.Event()
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(warmer.run, stop)
                try:
                    yield
                finally:
                    stop.set()
        else:
            yield
    finally:
        with anyio.CancelScope(shield=True):
            await warm_candidates.checkpoint()


def build_cors_config(origins: list[str]) -> CORSConfig | None:
    """Return a CORS config only when origins are explicitly configured."""
    if not origins:
        return None
    return CORSConfig(allow_origins=origins)


middleware = [
    LoggingMiddlewareConfig(
        exclude=r"^/solver/v1$",
        request_log_fields=("path", "method", "content_type"),
        response_log_fields=("status_code",),
    ).middleware
]
if RATE_LIMIT:
    middleware.append(RateLimitConfig(rate_limit=("minute", RATE_LIMIT)).middleware)


app = Litestar(
    route_handlers=[
        workbench,
        openapi_json,
        workbench_preflight,
        workbench_resolve,
        create_static_files_router(
            path="/assets",
            directories=[PACKAGE_DIR / "static"],
            cache_control=CacheControlHeader(
                max_age=31_536_000,
                public=True,
                immutable=True,
            ),
        ),
        resolve_get,
        resolve_post,
        preflight_post,
        repair_post,
        diff_post,
        explain_post,
        transcode_post,
        result_get,
        formats,
        platforms,
        version,
        parse,
        health,
        solver_v1,
    ],
    openapi_config=OpenAPIConfig(
        title="conda-presto",
        version=pkg_version("conda-presto"),
        description="Fast dry-run conda solver HTTP API.",
        path="/schema",
        render_plugins=[JsonRenderPlugin()],
    ),
    lifespan=[solver_resources_lifespan, solver_cache_refresher_lifespan],
    request_max_body_size=MAX_BODY_BYTES,
    compression_config=CompressionConfig(backend="brotli", brotli_gzip_fallback=True),
    cors_config=build_cors_config(CORS_ORIGINS),
    logging_config=LoggingConfig(
        log_exceptions="always",
        disable_stack_trace=set(range(400, 500)),
        loggers={"conda_presto": {"level": LOG_LEVEL}},
    ),
    middleware=middleware,
    template_config=TemplateConfig(
        directory=PACKAGE_DIR / "templates",
        engine=JinjaTemplateEngine,
    ),
)
