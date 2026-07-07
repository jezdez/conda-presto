"""Litestar HTTP API for conda environment resolving.

Endpoints:

- ``GET /resolve`` — resolve specs via query params
- ``POST /resolve`` — resolve specs and/or file content via JSON body
- ``POST /transcode`` — convert one lockfile format to another
- ``GET /r/{hash}`` — fetch a stored content-addressed resolve result
- ``GET /formats`` — list registered output format names
- ``GET /platforms`` — list known conda platform subdirs
- ``GET /version`` — version info for conda-presto and dependencies
- ``POST /parse`` — extract specs/channels from a file without solving
- ``GET /health`` — returns ``{"status": "ok"}``
- ``GET /`` — interactive Scalar API documentation
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
    - The ``on_startup`` hook pre-warms repodata caches so the first
      request doesn't pay cold-start costs.
    - Response compression (brotli with gzip fallback) reduces
      bandwidth for large solve results.
    - ``SolveResult`` / ``ResolvedPackage`` are ``msgspec.Struct`` and
      returned directly; Litestar encodes them natively without an
      intermediate dict conversion.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from importlib.metadata import version as pkg_version
from pathlib import Path

import anyio
import msgspec
from conda.core.subdir_data import SubdirData
from conda.models.channel import Channel
from litestar import Litestar, Request, get, post
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.logging import LoggingConfig
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig
from litestar.params import FromPath, FromQuery
from litestar.response import Response
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_504_GATEWAY_TIMEOUT,
)
from litestar.stores.base import Store
from litestar.stores.file import FileStore

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
    RESULT_CACHE_BACKEND,
    RESULT_CACHE_DIR,
    RESULT_CACHE_MAX_MEMORY_BYTES,
    RESULT_CACHE_REDIS_NAMESPACE,
    RESULT_CACHE_REDIS_URL,
    RESULT_CACHE_SIZE,
    SOLVE_TIMEOUT_S,
)
from .exceptions import UnknownFormatError
from .exporter import OutputFormat
from .inputs import ParsedInputFile
from .resolve import (
    NATIVE_SUBDIR,
    shutdown_process_pool,
    solve,
    solve_environments,
    warmup,
)

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

RESULT_CACHE_CONTROL = "public, max-age=86400, immutable"
DEFAULT_RESOLVE_FORMAT = "conda-presto-json-v1"
CACHE_ENVELOPE_VERSION = 1
RESULT_CACHE_STORE_NAME = "result_cache"
RESULT_CACHE_STORE_PREFIX = "resolve-v1:"


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


@dataclass
class TranscodeRequest:
    """JSON body for ``POST /transcode``."""

    file: str | None = None
    filename: str | None = None
    platforms: list[str] | None = None
    specs: list[str] | None = None
    channels: list[str] | None = None


class StoredResult(msgspec.Struct):
    body: bytes
    media_type: str

    @property
    def memory_size(self) -> int:
        return len(self.body) + len(self.media_type)


@dataclass
class ResultCache:
    max_size: int
    max_bytes: int = 0
    store_name: str | None = None
    entries: OrderedDict[str, StoredResult] = field(default_factory=OrderedDict)
    current_bytes: int = 0

    @classmethod
    def key_for(
        cls,
        specs: list[str],
        channels: list[str],
        platforms: list[str] | None,
        format_name: str | None,
    ) -> str:
        """Return the SHA-256 key for a canonical resolve request."""
        resolved_platforms = list(platforms or [NATIVE_SUBDIR])
        versions: dict[str, str] = {}
        for package in ("conda-presto", "conda", "conda-rattler-solver"):
            try:
                versions[package] = pkg_version(package)
            except Exception:
                versions[package] = "unknown"

        repodata: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        for channel in channels:
            for platform in resolved_platforms:
                for url in Channel(channel).urls(subdirs=(platform, "noarch")):
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    subdir_data = SubdirData(
                        Channel.from_url(url),
                        repodata_fn="repodata.json",
                    )
                    repodata.append(
                        {
                            "url": url,
                            "json": cls.file_marker(subdir_data.cache_path_json),
                            "state": cls.file_marker(subdir_data.cache_path_state),
                        }
                    )

        envelope = {
            "version": CACHE_ENVELOPE_VERSION,
            "specs": sorted(specs),
            "channels": list(channels),
            "platforms": resolved_platforms,
            "format": format_name or DEFAULT_RESOLVE_FORMAT,
            "dependency_versions": versions,
            "repodata": repodata,
        }
        body = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def file_marker(path: Path) -> dict[str, object]:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return {"exists": False}
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    def store_from(self, request: Request) -> Store | None:
        """Return the configured persistent store, if enabled."""
        if self.store_name is None:
            return None
        return request.app.stores.get(self.store_name)

    @staticmethod
    def store_key(key: str) -> str:
        return f"{RESULT_CACHE_STORE_PREFIX}{key}"

    @classmethod
    def stores_for_config(
        cls,
        backend: str,
        cache_dir: str | None,
        redis_url: str | None,
        redis_namespace: str,
    ) -> dict[str, Store]:
        if backend == "memory":
            return {}
        if backend == "file":
            if cache_dir is None:
                raise ValueError(
                    "CONDA_PRESTO_RESULT_CACHE_DIR is required "
                    "when CONDA_PRESTO_RESULT_CACHE_BACKEND=file"
                )
            return {
                RESULT_CACHE_STORE_NAME: FileStore(
                    Path(cache_dir),
                    create_directories=True,
                )
            }
        if backend == "redis":
            try:
                from litestar.stores.redis import RedisStore
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Redis result cache requires the redis-py package. "
                    "Install conda-presto with the redis extra or use "
                    "the Pixi redis environment."
                ) from exc
            return {
                RESULT_CACHE_STORE_NAME: RedisStore.with_client(
                    url=redis_url or "redis://localhost:6379/0",
                    namespace=redis_namespace,
                )
            }
        raise ValueError(f"Unsupported result cache backend: {backend}")

    def remember_memory(self, key: str, stored: StoredResult) -> bool:
        if self.max_bytes > 0 and stored.memory_size > self.max_bytes:
            if previous := self.entries.pop(key, None):
                self.current_bytes -= previous.memory_size
            return False

        if previous := self.entries.get(key):
            self.current_bytes -= previous.memory_size

        self.entries[key] = stored
        self.current_bytes += stored.memory_size
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_size or (
            self.max_bytes > 0 and self.current_bytes > self.max_bytes
        ):
            _, evicted = self.entries.popitem(last=False)
            self.current_bytes -= evicted.memory_size
        return key in self.entries

    async def get_response(
        self, key: str, store: Store | None = None
    ) -> Response | None:
        stored = self.entries.get(key)
        if stored is not None:
            self.entries.move_to_end(key)
            return self.response_for(key, stored)

        if store is None:
            return None

        try:
            stored_payload = await store.get(self.store_key(key))
        except Exception:
            log.warning("Persistent result cache read failed", exc_info=True)
            return None
        if stored_payload is None:
            return None

        try:
            stored = msgspec.msgpack.decode(stored_payload, type=StoredResult)
        except (msgspec.DecodeError, msgspec.ValidationError):
            log.warning("Ignoring corrupt persistent result cache entry for %s", key)
            await store.delete(self.store_key(key))
            return None

        self.remember_memory(key, stored)
        return self.response_for(key, stored)

    async def remember(
        self,
        key: str,
        body: bytes,
        media_type: str,
        store: Store | None = None,
    ) -> Response:
        stored = StoredResult(body=body, media_type=media_type)
        retained = self.remember_memory(key, stored)
        if store is not None:
            try:
                await store.set(self.store_key(key), msgspec.msgpack.encode(stored))
                retained = True
            except Exception:
                log.warning("Persistent result cache write failed", exc_info=True)
        if retained:
            return self.response_for(key, stored)
        return Response(stored.body, media_type=stored.media_type)

    @staticmethod
    def response_for(key: str, stored: StoredResult) -> Response:
        return Response(
            stored.body,
            media_type=stored.media_type,
            headers={
                "Location": f"/r/{key}",
                "Cache-Control": RESULT_CACHE_CONTROL,
            },
        )


def validate_caps(specs: list[str], platforms: list[str]) -> Response | None:
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
) -> Response | tuple[bytes, str]:
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
        return OutputFormat.named(format_name).render(envs)

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
        return msgspec.json.encode(result), "application/json"
    body, media_type = result
    if isinstance(body, str):
        body = body.encode()
    return body, media_type


async def run_cached_solve(
    request: Request,
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
    format_name: str | None = None,
) -> Response:
    """Run a solve through the content-addressed result cache."""
    cache: ResultCache = request.app.state.result_cache
    store = cache.store_from(request)
    key = cache.key_for(specs, channels, platforms, format_name)
    if cached_response := await cache.get_response(key, store):
        return cached_response

    payload = await run_solve(
        request, specs, channels, platforms, format_name=format_name
    )
    if isinstance(payload, Response):
        return payload

    # Recompute after solving so a cold repodata cache stores under the
    # marker that exists after conda has fetched repodata.
    key = cache.key_for(specs, channels, platforms, format_name)
    body, media_type = payload
    return await cache.remember(key, body, media_type, store)


def transcode_rejection(
    parsed: ParsedInputFile | None,
    format_name: str | None,
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
    if format_name is None:
        reasons.append("no output format was requested")
    else:
        try:
            output_format = OutputFormat.named(format_name)
        except UnknownFormatError as exc:
            return Response(
                {"error": str(exc), "available_formats": exc.available},
                status_code=HTTP_400_BAD_REQUEST,
            )
        if not output_format.is_lockfile:
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

    if cap_error := validate_caps(specs, platforms):
        return cap_error

    if not channels:
        channels = list(DEFAULT_CHANNELS)

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
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    file_content: str | None = None
    file_name: str | None = None
    parsed_file: ParsedInputFile | None = None

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
        channels = data.channels if data.channels is not None else (channel or [])
        platforms = data.platforms if data.platforms is not None else (platform or [])
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
                    "for a raw input file body."
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
            parsed_file = ParsedInputFile.from_content(
                file_content, file_name, platforms or [NATIVE_SUBDIR]
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status_code=HTTP_400_BAD_REQUEST)

        specs = list(specs) + parsed_file.specs
        if not channels:
            channels = parsed_file.channels

    if not specs:
        if parsed_file and parsed_file.is_lockfile:
            return Response(
                {
                    "error": (
                        "Lockfile input cannot be solved for the "
                        "requested platforms; request a lockfile output "
                        "for a platform present in the lockfile or "
                        "provide specs to solve."
                    )
                },
                status_code=HTTP_400_BAD_REQUEST,
            )
        return Response(
            {"error": "Provide specs or file content"},
            status_code=HTTP_400_BAD_REQUEST,
        )

    if cap_error := validate_caps(specs, platforms):
        return cap_error

    if not channels:
        channels = list(DEFAULT_CHANNELS)

    return await run_cached_solve(
        request, specs, channels, platforms or None, format_name=format
    )


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
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

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
    if cap_error := validate_caps([], target_platforms):
        return cap_error

    has_extra_specs = bool(spec) or bool(body_specs)
    has_channel_override = bool(channel) or bool(body_channels)
    if file_content is None:
        return transcode_rejection(
            None,
            format,
            target_platforms,
            has_extra_specs,
            has_channel_override,
        )

    try:
        parsed_file = ParsedInputFile.from_content(
            file_content, file_name, target_platforms
        )
    except ValueError as exc:
        return Response({"error": str(exc)}, status_code=HTTP_400_BAD_REQUEST)

    if (
        parsed_file.is_lockfile
        and format is not None
        and not has_extra_specs
        and not has_channel_override
    ):
        try:
            output_format = OutputFormat.named(format)
        except UnknownFormatError as exc:
            return Response(
                {"error": str(exc), "available_formats": exc.available},
                status_code=HTTP_400_BAD_REQUEST,
            )
        if output_format.is_lockfile and parsed_file.environments:
            try:
                body, media_type = output_format.render(list(parsed_file.environments))
            except UnknownFormatError as exc:
                return Response(
                    {"error": str(exc), "available_formats": exc.available},
                    status_code=HTTP_400_BAD_REQUEST,
                )
            except Exception:
                log.exception("Environment export failed")
                return Response(
                    {"error": "Internal solver error"},
                    status_code=HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(body, media_type=media_type)

    return transcode_rejection(
        parsed_file,
        format,
        target_platforms,
        has_extra_specs,
        has_channel_override,
    )


@get("/r/{key:str}")
async def result_get(request: Request, key: FromPath[str]) -> Response:
    """Return a stored content-addressed solve result."""
    cache: ResultCache = request.app.state.result_cache
    cached_response = await cache.get_response(key, cache.store_from(request))
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
    from conda.base.constants import KNOWN_SUBDIRS

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
)
async def parse(request: Request) -> Response:
    """Parse an input file and return its specs and channels."""
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
        parsed_file = ParsedInputFile.from_content(data.file, data.filename)
    except ValueError as exc:
        return Response(
            {"error": str(exc)},
            status_code=HTTP_400_BAD_REQUEST,
        )
    return Response({"specs": parsed_file.specs, "channels": parsed_file.channels})


@get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


async def on_startup(app: Litestar) -> None:
    """Initialize solver limiter and pre-warm repodata caches."""
    app.state.solver_limiter = anyio.CapacityLimiter(MAX_CONCURRENCY)
    app.state.result_cache = ResultCache(
        max_size=RESULT_CACHE_SIZE,
        max_bytes=RESULT_CACHE_MAX_MEMORY_BYTES,
        store_name=(
            RESULT_CACHE_STORE_NAME if RESULT_CACHE_BACKEND != "memory" else None
        ),
    )
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
    middleware.append(RateLimitConfig(rate_limit=("minute", RATE_LIMIT)).middleware)


app = Litestar(
    route_handlers=[
        resolve_get,
        resolve_post,
        transcode_post,
        result_get,
        formats,
        platforms,
        version,
        parse,
        health,
    ],
    openapi_config=OpenAPIConfig(
        title="conda-presto",
        version=pkg_version("conda-presto"),
        description="Fast dry-run conda solver HTTP API.",
        path="/",
    ),
    on_startup=[on_startup],
    on_shutdown=[on_shutdown],
    stores=ResultCache.stores_for_config(
        RESULT_CACHE_BACKEND,
        RESULT_CACHE_DIR,
        RESULT_CACHE_REDIS_URL,
        RESULT_CACHE_REDIS_NAMESPACE,
    ),
    request_max_body_size=MAX_BODY_BYTES,
    compression_config=CompressionConfig(backend="brotli", brotli_gzip_fallback=True),
    cors_config=CORSConfig(allow_origins=CORS_ORIGINS),
    logging_config=LoggingConfig(
        log_exceptions="always",
        loggers={"conda_presto": {"level": LOG_LEVEL}},
    ),
    middleware=middleware,
)
