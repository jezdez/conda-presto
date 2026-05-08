"""Litestar HTTP API for conda environment resolving.

Endpoints:

- ``GET /resolve`` — resolve specs via query params
- ``POST /resolve`` — resolve specs and/or file content via JSON body
- ``GET /formats`` — list registered output format names
- ``GET /platforms`` — list known conda platform subdirs
- ``GET /version`` — version info for conda-presto and dependencies
- ``POST /lint`` — lint an environment file for common issues
- ``POST /parse`` — extract specs/channels from a file without solving
- ``GET /health`` — returns ``{"status": "ok"}``
- ``GET /`` — interactive Scalar API documentation
- ``GET /openapi.json`` — OpenAPI 3.1 schema (auto-generated)
- ``POST /mcp`` — MCP Streamable HTTP endpoint (via ``litestar-mcp``)
- ``GET /.well-known/mcp-server.json`` — MCP server manifest

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
    - The ``on_startup`` hook pre-warms repodata caches so the first
      request doesn't pay cold-start costs.
    - Response compression (brotli with gzip fallback) reduces
      bandwidth for large solve results.
    - ``SolveResult`` / ``ResolvedPackage`` are ``msgspec.Struct`` and
      returned directly; Litestar encodes them natively without an
      intermediate dict conversion.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from importlib.metadata import version as pkg_version

import anyio
import msgspec
from conda.base.constants import KNOWN_SUBDIRS
from conda.base.context import context
from litestar import Litestar, Request, get, post
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.logging import LoggingConfig
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.response import Response
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_504_GATEWAY_TIMEOUT,
)
from litestar_mcp import LitestarMCP, MCPConfig

from .config import (
    CORS_ORIGINS,
    DEFAULT_CHANNELS,
    DEFAULT_PLATFORMS,
    LOG_LEVEL,
    MAX_BODY_BYTES,
    MAX_CONCURRENCY,
    MAX_PLATFORMS,
    MAX_SPECS,
    RATE_LIMIT,
    SOLVE_TIMEOUT_S,
)
from .exceptions import UnknownFormatError
from .exporter import available_formats, render_envs
from .lint import lint
from .resolve import (
    shutdown_process_pool,
    solve,
    solve_environments,
    warmup,
)

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".yml", ".yaml", ".txt", ".lock", ".toml", ".json"}

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


def parse_file_content(
    content: str,
    filename: str | None = None,
) -> tuple[list[str], list[str]]:
    """Parse file content through conda's env spec plugin system.

    Writes *content* to a temp file and runs it through
    ``detect_environment_specifier``, the same codepath the CLI uses.
    Returns ``(specs, channels)``.

    The *filename* controls which parser conda selects (via extension).
    Only extensions in ``ALLOWED_EXTENSIONS`` are accepted.  Directory
    components are stripped to prevent path traversal.

    The tempfile is kept alive for the entire duration of attribute
    access on the parsed environment, since some env-spec plugins read
    the file lazily on ``requested_packages`` / ``config.channels``
    access.
    """
    filename = os.path.basename(filename or "environment.yml")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file extension '{ext}', "
            f"allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=ext, delete=True
    ) as tmp:
        tmp.write(content)
        tmp.flush()

        spec_plugin = context.plugin_manager.detect_environment_specifier(
            tmp.name
        )
        spec = spec_plugin.environment_spec(filename=tmp.name)
        if not spec.can_handle():
            raise ValueError(
                f"No conda environment spec plugin can handle "
                f"this file format ({ext})"
            )
        env = spec.env
        specs = [str(s) for s in env.requested_packages]
        channels: list[str] = []
        if env.config and env.config.channels:
            channels.extend(env.config.channels)
    return specs, channels


def validate_caps(
    specs: list[str], platforms: list[str]
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
    return None


async def run_solve(
    request: Request,
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
    format_name: str | None = None,
) -> Response:
    """Shared solve runner: threadpool + timeout + error sanitization.

    When *format_name* is ``None``, runs the native path
    (``solve`` → ``list[SolveResult]`` as JSON).  When set, runs the
    exporter path (``solve_environments`` → conda exporter plugin →
    string body with a format-appropriate ``Content-Type``).
    """

    def work():
        if format_name is None:
            return solve(channels, specs, platforms)
        envs = solve_environments(channels, specs, platforms)
        return render_envs(envs, format_name)

    try:
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            result = await anyio.to_thread.run_sync(
                work,
                limiter=request.app.state.solver_limiter,
                abandon_on_cancel=True,
            )
    except TimeoutError:
        log.warning(
            "Solve timeout after %ss (specs=%d platforms=%s format=%s)",
            SOLVE_TIMEOUT_S,
            len(specs),
            platforms,
            format_name,
        )
        return Response(
            {"error": f"Solve exceeded {SOLVE_TIMEOUT_S}s timeout"},
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
        return Response(result)
    body, media_type = result
    return Response(body, media_type=media_type)


@get(
    "/resolve",
    mcp_tool="resolve",
    mcp_description=(
        "Resolve conda package specs to fully pinned packages."
    ),
    mcp_when_to_use=(
        "Use when you need to dry-run a conda solve "
        "without installing anything."
    ),
    mcp_returns=(
        "Resolved packages with versions, builds, "
        "channels, and SHA256 hashes."
    ),
    mcp_agent_instructions=(
        "Pass specs as repeated ?spec= query params "
        "(e.g. ?spec=python=3.12&spec=numpy). "
        "Channels default to conda-forge. "
        "Use ?platform= to solve for specific platforms "
        "(linux-64, osx-arm64, win-64, etc.); omit for native. "
        "Use ?format= to get output in a specific format: "
        "explicit, environment-yaml, environment-json, "
        "conda-lock-v1, pixi-lock-v6, or requirements."
    ),
)
async def resolve_get(
    request: Request,
    spec: list[str] | None = None,
    channel: list[str] | None = None,
    platform: list[str] | None = None,
    format: str | None = None,
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

    if cap_error := validate_caps(specs, platforms):
        return cap_error

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    return await run_solve(
        request, specs, channels, platforms or None, format_name=format
    )


@post(
    "/resolve",
    status_code=200,
    mcp_tool="resolve_file",
    mcp_description=(
        "Resolve a conda environment file or inline specs "
        "to fully pinned packages."
    ),
    mcp_when_to_use=(
        "Use when you have an environment.yml, pixi.lock, "
        "or other environment file to resolve."
    ),
    mcp_returns=(
        "Resolved packages or a rendered lockfile "
        "in the requested format."
    ),
    mcp_agent_instructions=(
        "POST a JSON body with: "
        "specs (list of strings), "
        "file (string content of an environment file), "
        "filename (e.g. 'environment.yml' or 'pixi.lock' — "
        "controls which parser is used), "
        "channels (list, default conda-forge), "
        "platforms (list, e.g. ['linux-64', 'osx-arm64']). "
        "Or POST the raw file as the body with "
        "Content-Type: application/yaml. "
        "Use ?format= for output format: "
        "explicit, environment-yaml, environment-json, "
        "conda-lock-v1, pixi-lock-v6, or requirements. "
        "Supported input formats: environment.yml, "
        "pyproject.toml, requirements.txt, pixi.lock, "
        "conda-lock.yml, explicit lockfiles."
    ),
)
async def resolve_post(
    request: Request,
    spec: list[str] | None = None,
    channel: list[str] | None = None,
    platform: list[str] | None = None,
    format: str | None = None,
    filename: str | None = None,
) -> Response:
    """Resolve package specs and/or an environment file via POST body.

    Dispatch on ``Content-Type``:

    * ``application/json`` (or missing): body is a :class:`ResolveRequest`
      envelope.  Body fields override query params by presence — an
      explicit empty array in the body overrides the corresponding
      query param; an omitted field falls through.
    * ``application/yaml`` / ``application/x-yaml`` / ``text/yaml`` /
      ``application/toml`` / ``text/plain``: the body *is* the raw
      environment file content (e.g. an ``environment.yml``).  Specs,
      channels, and platforms come from query params only.  The
      parser is picked from ``Content-Type``; pass ``?filename=`` to
      override (e.g. ``?filename=pixi.lock`` to force the lockfile
      parser when Content-Type is a generic YAML).

    Pass ``?format=<name>`` on either dispatch to route the response
    through conda's exporter plugin registry.  ``format`` is
    query-only; it is not read from the JSON body.
    """
    content_type = (
        request.headers.get("content-type", "")
        .split(";")[0]
        .strip()
        .lower()
    )

    file_content: str | None = None
    file_name: str | None = None

    if content_type in ("", "application/json"):
        body = await request.body()
        if body:
            try:
                data = msgspec.json.decode(body, type=ResolveRequest)
            except (msgspec.DecodeError, msgspec.ValidationError) as exc:
                return Response(
                    {"error": f"Invalid JSON body: {exc}"},
                    status_code=HTTP_400_BAD_REQUEST,
                )
        else:
            data = ResolveRequest()

        specs = data.specs if data.specs is not None else (spec or [])
        channels = (
            data.channels if data.channels is not None else (channel or [])
        )
        platforms = (
            data.platforms
            if data.platforms is not None
            else (platform or [])
        )
        file_content = data.file
        file_name = data.filename or filename
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
        specs = spec or []
        channels = channel or []
        platforms = platform or []
    else:
        return Response(
            {
                "error": (
                    f"Unsupported Content-Type {content_type!r}. "
                    "Use application/json for a ResolveRequest envelope, "
                    "or application/yaml / application/toml / text/plain "
                    "for a raw environment file body."
                ),
                "supported": [
                    "application/json",
                    *sorted(RAW_CONTENT_TYPE_EXTENSIONS),
                ],
            },
            status_code=HTTP_400_BAD_REQUEST,
        )

    if file_content is not None:
        try:
            file_specs, file_channels = parse_file_content(
                file_content, file_name
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc)}, status_code=HTTP_400_BAD_REQUEST
            )
        specs = list(specs) + file_specs
        if not channels:
            channels = file_channels

    if not specs:
        return Response(
            {"error": "Provide specs or file content"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if cap_error := validate_caps(specs, platforms):
        return cap_error

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    return await run_solve(
        request, specs, channels, platforms or None, format_name=format
    )


@get(
    "/formats",
    mcp_resource="formats",
    mcp_description=(
        "List all supported output format names for "
        "the ?format= query parameter."
    ),
)
async def formats() -> dict[str, list[str]]:
    """Return the list of registered exporter format names."""
    return {"formats": available_formats()}


@get(
    "/platforms",
    mcp_resource="platforms",
    mcp_description="List all known conda platform subdirs.",
)
async def platforms() -> dict[str, list[str]]:
    """Return the known conda platform subdirectory names."""
    return {"platforms": sorted(KNOWN_SUBDIRS)}


@get(
    "/version",
    mcp_resource="version",
    mcp_description=(
        "Return conda-presto, conda, solver, and plugin versions."
    ),
)
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
    mcp_tool="parse_file",
    mcp_description=(
        "Parse an environment file and extract its specs, "
        "channels, and name without solving."
    ),
    mcp_when_to_use=(
        "Use to inspect what is in an environment file "
        "before deciding whether to solve."
    ),
    mcp_returns=(
        "Extracted specs, channels, and environment name."
    ),
    mcp_agent_instructions=(
        "POST a JSON body with: "
        "file (string content of an environment file), "
        "filename (e.g. 'environment.yml' — controls parser). "
        "Returns {specs, channels} without running a solve."
    ),
)
async def parse(request: Request) -> Response:
    """Parse an environment file and return its specs and channels."""
    body = await request.body()
    if not body:
        return Response(
            {"error": "Provide a JSON body with 'file' content"},
            status_code=HTTP_400_BAD_REQUEST,
        )
    try:
        data = msgspec.json.decode(body, type=ResolveRequest)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        return Response(
            {"error": f"Invalid JSON body: {exc}"},
            status_code=HTTP_400_BAD_REQUEST,
        )
    if not data.file:
        return Response(
            {"error": "Field 'file' is required"},
            status_code=HTTP_400_BAD_REQUEST,
        )
    try:
        specs, channels = parse_file_content(data.file, data.filename)
    except ValueError as exc:
        return Response(
            {"error": str(exc)},
            status_code=HTTP_400_BAD_REQUEST,
        )
    return Response({"specs": specs, "channels": channels})


@post(
    "/lint",
    status_code=200,
    mcp_tool="lint",
    mcp_description=(
        "Lint a conda environment file for common issues "
        "without solving or making network calls."
    ),
    mcp_when_to_use=(
        "Use to check an environment.yml for pinning issues, "
        "duplicate packages, formatting problems, or missing "
        "fields before solving."
    ),
    mcp_returns=(
        "A list of findings with severity, line numbers, "
        "machine-readable codes, and optional fix suggestions."
    ),
    mcp_agent_instructions=(
        "POST a JSON body with: "
        "file (string content of an environment.yml), "
        "filename (optional, e.g. 'environment.yml'). "
        "Optional query params: "
        "?ignore=PIN001,FMT002 to skip specific rules, "
        "?severity=warning to filter to >= severity. "
        "Returns {findings: [...], summary: {errors, warnings, info}}."
    ),
)
async def lint_endpoint(
    request: Request,
    ignore: str | None = None,
    severity: str | None = None,
) -> Response:
    """Lint an environment file for common issues.

    Pure-function check: no solver, no network, no conda imports.
    Sub-50 ms for typical files.

    Accepts the same JSON body format as ``/parse``
    (``{"file": "...", "filename": "..."}``) or a raw file body
    with an appropriate ``Content-Type``.

    Pass ``?ignore=PIN001,FMT002`` to skip specific rules.
    Pass ``?severity=warning`` to filter findings to that level
    or higher (``info`` < ``warning`` < ``error``).
    """
    content_type = (
        request.headers.get("content-type", "")
        .split(";")[0]
        .strip()
        .lower()
    )

    file_content: str | None = None
    file_name: str | None = None

    if content_type in ("", "application/json"):
        body = await request.body()
        if body:
            try:
                data = msgspec.json.decode(body, type=ResolveRequest)
            except (msgspec.DecodeError, msgspec.ValidationError) as exc:
                return Response(
                    {"error": f"Invalid JSON body: {exc}"},
                    status_code=HTTP_400_BAD_REQUEST,
                )
            file_content = data.file
            file_name = data.filename
        else:
            return Response(
                {"error": "Provide a JSON body with 'file' content"},
                status_code=HTTP_400_BAD_REQUEST,
            )
    elif content_type in RAW_CONTENT_TYPE_EXTENSIONS:
        body = await request.body()
        try:
            file_content = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            return Response(
                {"error": f"Body is not valid UTF-8: {exc}"},
                status_code=HTTP_400_BAD_REQUEST,
            )
        file_name = f"environment{RAW_CONTENT_TYPE_EXTENSIONS[content_type]}"
    else:
        return Response(
            {"error": f"Unsupported Content-Type {content_type!r}"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if not file_content:
        return Response(
            {"error": "Field 'file' is required"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    ignore_set = (
        {c.strip() for c in ignore.split(",") if c.strip()}
        if ignore
        else None
    )

    result = lint(
        file_content,
        filename=file_name,
        ignore=ignore_set,
        min_severity=severity,
    )
    return Response(result)


@get("/health", mcp_resource="health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


async def on_startup(app: Litestar) -> None:
    """Initialize solver limiter and pre-warm repodata caches."""
    app.state.solver_limiter = anyio.CapacityLimiter(MAX_CONCURRENCY)
    log.info(
        "Pre-warming repodata cache for %s on %s",
        DEFAULT_CHANNELS,
        DEFAULT_PLATFORMS,
    )
    await anyio.to_thread.run_sync(
        lambda: warmup(DEFAULT_CHANNELS, DEFAULT_PLATFORMS),
        abandon_on_cancel=True,
    )
    log.info("Repodata cache warm")


async def on_shutdown(app: Litestar) -> None:
    """Cleanly shut down the process pool on server teardown."""
    shutdown_process_pool()


middleware = [LoggingMiddlewareConfig().middleware]
if RATE_LIMIT:
    middleware.append(
        RateLimitConfig(rate_limit=("minute", RATE_LIMIT)).middleware
    )

app = Litestar(
    route_handlers=[
        resolve_get,
        resolve_post,
        formats,
        platforms,
        version,
        parse,
        lint_endpoint,
        health,
    ],
    plugins=[LitestarMCP(MCPConfig(name="conda-presto"))],
    openapi_config=OpenAPIConfig(
        title="conda-presto",
        version=pkg_version("conda-presto"),
        description="Fast dry-run conda solver HTTP API.",
        path="/",
    ),
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
    request_max_body_size=MAX_BODY_BYTES,
    compression_config=CompressionConfig(
        backend="brotli", brotli_gzip_fallback=True
    ),
    cors_config=CORSConfig(allow_origins=CORS_ORIGINS),
    logging_config=LoggingConfig(
        log_exceptions="always",
        loggers={"conda_presto": {"level": LOG_LEVEL}},
    ),
    middleware=middleware,
)
