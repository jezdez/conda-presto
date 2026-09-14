"""HTTP access to conda solving, parsing, exporting and retained results.

Requests use bounded isolated execution. Successful solve outputs can be
retrieved while their cache entry remains available. Exporters come from
conda's plugin registry. The root serves the generated OpenAPI document.
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import anyio
import msgspec
from conda.base.constants import KNOWN_SUBDIRS
from conda.exceptions import CondaError
from conda.models.channel import Channel
from conda_workspaces.lockfile import LOCKFILE_NAME
from litestar import Litestar, Request, get, post
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.logging import LoggingConfig
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.middleware.rate_limit import RateLimitConfig
from litestar.openapi import OpenAPIConfig, ResponseSpec
from litestar.openapi.plugins import JsonRenderPlugin
from litestar.params import (
    FromPath,
    FromQuery,
)
from litestar.response import Response
from litestar.status_codes import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_503_SERVICE_UNAVAILABLE,
    HTTP_504_GATEWAY_TIMEOUT,
)

from .attestation import AttestationError, AttestationService
from .cache import ResultCache
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
    SIGSTORE_ALLOW_PUBLIC_SIGNING,
    SIGSTORE_OFFLINE,
    SIGSTORE_SIGNING_ENABLED,
    SIGSTORE_TRUST_CONFIG,
    SOLVE_TIMEOUT_S,
)
from .exceptions import (
    CredentialRedactionFilter,
    UnknownFormatError,
    WorkspaceSolveError,
)
from .exporter import ExporterCacheIdentity, OutputFormat
from .inputs import ParsedInputFile, ParseResult, WorkspaceParseResult
from .resolve import (
    NATIVE_SUBDIR,
    RepodataSnapshot,
    shutdown_process_pool,
    solve,
    solve_environments,
    warmup,
)
from .storage import StoreOperationCoordinator
from .worker import PersistentSolveWorker
from .workspace import WorkspaceInput
from .workspace_lock import WorkspaceLockCheckResult, WorkspaceLockParseResult

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
    environments: list[str] | None = None

    @classmethod
    async def from_http(
        cls,
        request: Request,
        spec: list[str] | None = None,
        channel: list[str] | None = None,
        platform: list[str] | None = None,
        filename: str | None = None,
        environment: list[str] | None = None,
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
                platforms=(data.platforms if data.platforms is not None else platform),
                environments=(
                    data.environments if data.environments is not None else environment
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
                platforms=platform,
                environments=environment,
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

    async def inputs(
        self, request: Request, *, allow_lockfile: bool = True
    ) -> ResolveRequest | WorkspaceInput | Response:
        """Parse the uploaded file and apply shared solve defaults and limits."""
        file_content = self.file
        file_name = self.filename
        specs = self.specs or []
        channels = self.channels or []
        platforms = self.platforms or []
        parsed_file: ParsedInputFile | None = None

        if file_content is not None:
            parsed = await parse_input_for_request(
                request,
                file_content,
                file_name,
                self.platforms,
                target_environments=self.environments,
            )
            if isinstance(parsed, Response):
                return parsed
            parsed_file = parsed
            if parsed_file.workspace_lock is not None and allow_lockfile:
                return Response(
                    ErrorResponse(
                        error=(
                            "Workspace lockfiles cannot be solved. "
                            "Use POST /export to export their exact locked records."
                        )
                    ),
                    status_code=HTTP_400_BAD_REQUEST,
                )
            if parsed_file.workspace is not None:
                if allow_lockfile and not specs and not channels:
                    try:
                        workspace = parsed_file.workspace.select(
                            self.environments, self.platforms
                        )
                    except ValueError as exc:
                        return Response(
                            ErrorResponse(error=str(exc)),
                            status_code=HTTP_400_BAD_REQUEST,
                        )
                    for target in workspace.result.selected:
                        if cap_error := validate_caps(
                            target.specs, target.channels, [target.subdir]
                        ):
                            return cap_error
                    return workspace
                return Response(
                    ErrorResponse(
                        error=(
                            "Workspace solves do not accept extra specs "
                            "or channel overrides."
                            if allow_lockfile
                            else "Workspace SBOM requests are not supported yet."
                        )
                    ),
                    status_code=HTTP_400_BAD_REQUEST,
                )
            if parsed_file.is_lockfile and not allow_lockfile:
                return Response(
                    ErrorResponse(
                        error=("Locked SBOMs require a workspace conda.lock file.")
                    ),
                    status_code=HTTP_400_BAD_REQUEST,
                )

            specs = list(specs) + parsed_file.specs
            if not channels:
                channels = parsed_file.channels

        if self.environments is not None:
            return Response(
                ErrorResponse(
                    error="Environment selection requires a workspace manifest"
                ),
                status_code=HTTP_400_BAD_REQUEST,
            )

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

        return replace(self, specs=specs, channels=channels, platforms=platforms)


@dataclass
class ExportRequest:
    """Uploaded input and selectors for conversion without solving."""

    file: str | None = None
    filename: str | None = None
    platforms: list[str] | None = None
    environments: list[str] | None = None
    specs: list[str] | None = None
    channels: list[str] | None = None
    manifest: str | None = None
    manifest_filename: str | None = None

    async def read(self, request: Request) -> ExportRequest | Response:
        """Read uploaded content, using this request for query defaults."""
        content_type, _ = request.content_type

        file_content: str | None = None
        file_name: str | None = None
        platforms = self.platforms
        environments = self.environments
        body_specs: list[str] = []
        body_channels: list[str] = []
        manifest = self.manifest
        manifest_filename = self.manifest_filename

        if content_type in ("", "application/json"):
            body = await request.body()
            if body:
                try:
                    data = msgspec.json.decode(body, type=ExportRequest)
                except (msgspec.DecodeError, msgspec.ValidationError) as exc:
                    return Response(
                        {"error": f"Invalid JSON body: {exc}"},
                        status_code=HTTP_400_BAD_REQUEST,
                    )
            else:
                data = ExportRequest()

            file_content = data.file
            file_name = data.filename or self.filename
            platforms = data.platforms if data.platforms is not None else platforms
            environments = (
                data.environments if data.environments is not None else environments
            )
            body_specs = data.specs or []
            body_channels = data.channels or []
            manifest = data.manifest
            manifest_filename = data.manifest_filename
        elif content_type in RAW_CONTENT_TYPE_EXTENSIONS:
            body = await request.body()
            try:
                file_content = body.decode("utf-8")
            except UnicodeDecodeError as exc:
                return Response(
                    {"error": f"Body is not valid UTF-8: {exc}"},
                    status_code=HTTP_400_BAD_REQUEST,
                )
            file_name = self.filename or (
                f"environment{RAW_CONTENT_TYPE_EXTENSIONS[content_type]}"
            )
        else:
            return Response(
                {
                    "error": (
                        f"Unsupported Content-Type {content_type!r}. "
                        "Use application/json for an export request envelope, "
                        "or application/yaml / application/toml / text/plain "
                        "for a raw input file."
                    ),
                    "supported": [
                        "application/json",
                        *sorted(RAW_CONTENT_TYPE_EXTENSIONS),
                    ],
                },
                status_code=HTTP_400_BAD_REQUEST,
            )

        return replace(
            self,
            file=file_content,
            filename=file_name,
            platforms=platforms,
            environments=environments,
            specs=(self.specs or []) + body_specs,
            channels=(self.channels or []) + body_channels,
            manifest=manifest,
            manifest_filename=manifest_filename,
        )

    async def response(
        self,
        request: Request,
        format_name: str | None,
        *,
        lockfile_only: bool = False,
    ) -> Response:
        """Export through the bounded parser and retain eligible locked outputs."""
        if lockfile_only and (
            self.manifest is not None or self.manifest_filename is not None
        ):
            return Response(
                ErrorResponse(error="Manifest context requires POST /export"),
                status_code=HTTP_400_BAD_REQUEST,
            )
        target_platforms = self.platforms or [NATIVE_SUBDIR]

        has_extra_specs = bool(self.specs)
        has_channel_override = bool(self.channels)
        output_format = None
        if format_name is not None:
            try:
                output_format = OutputFormat.named(format_name)
            except UnknownFormatError as exc:
                return Response(
                    {"error": str(exc), "available_formats": exc.available},
                    status_code=HTTP_400_BAD_REQUEST,
                )
        if self.file is None:
            return self.rejection(
                None, output_format, target_platforms, lockfile_only=lockfile_only
            )

        export_format = (
            output_format.exporter.name
            if output_format is not None
            and (output_format.is_lockfile or not lockfile_only)
            and not has_extra_specs
            and not has_channel_override
            else None
        )
        parsed = await parse_input_for_request(
            request,
            self.file,
            self.filename,
            self.platforms,
            export_format=export_format,
            lockfile_only=lockfile_only,
            target_environments=self.environments,
            manifest_content=self.manifest,
            manifest_filename=self.manifest_filename,
        )
        if isinstance(parsed, Response):
            return parsed
        parsed_file = parsed
        if parsed_file.workspace_lock is None and parsed_file.workspace is None:
            if cap_error := validate_caps(
                parsed_file.specs, parsed_file.channels, target_platforms
            ):
                return cap_error
        elif parsed_file.workspace_lock is not None:
            target_platforms = [
                target.platform for target in parsed_file.workspace_lock.result.selected
            ]

        if (
            (parsed_file.is_lockfile or not lockfile_only)
            and output_format is not None
            and not has_extra_specs
            and not has_channel_override
            and (output_format.is_lockfile or not lockfile_only)
            and parsed_file.exported_content is not None
        ):
            if parsed_file.workspace_lock is not None:
                cache: ResultCache = request.app.state.result_cache
                exporter_identity = output_format.cache_identity()
                identity = {
                    "operation": "transcode" if lockfile_only else "export",
                    "input": parsed_file.workspace_lock.cache_identity(),
                    "output": output_format.exporter.name,
                    "exporter": exporter_identity,
                }
                digest = hashlib.sha256(msgspec.json.encode(identity)).hexdigest()
                return await cache.remember(
                    cache.request_key(digest),
                    parsed_file.exported_content.encode("utf-8"),
                    output_format.media_type,
                    retain=exporter_identity is not None,
                )
            return Response(
                parsed_file.exported_content,
                media_type=output_format.media_type,
                headers={"Cache-Control": "no-store"},
            )

        return self.rejection(
            parsed_file, output_format, target_platforms, lockfile_only=lockfile_only
        )

    def rejection(
        self,
        parsed: ParsedInputFile | None,
        output_format: OutputFormat | None,
        target_platforms: list[str],
        *,
        lockfile_only: bool,
    ) -> Response:
        """Explain why the supplied input cannot be exported."""
        reasons: list[str] = []
        if parsed is None:
            reasons.append("no file input was provided")
        elif lockfile_only and not parsed.is_lockfile:
            reasons.append("input file is not a lockfile")
        elif parsed.is_lockfile:
            missing = sorted(set(target_platforms) - set(parsed.available_platforms))
            if missing:
                reasons.append(
                    "requested platforms not present in lockfile: " + ", ".join(missing)
                )
            elif (
                output_format is not None
                and (output_format.is_lockfile or not lockfile_only)
                and not self.specs
                and not self.channels
            ):
                if parsed.exported_content is None:
                    reasons.append(
                        "input lockfile format does not support no-download "
                        + ("transcoding" if lockfile_only else "exporting")
                    )
        elif output_format is not None and parsed.exported_content is None:
            reasons.append("input format does not support the requested export")
        if output_format is None:
            reasons.append("no output format was requested")
        elif not output_format.is_lockfile and lockfile_only:
            reasons.append("output format is not a lockfile")
        if self.specs:
            reasons.append("additional specs require solving")
        if self.channels:
            reasons.append("channel overrides require solving")
        return Response(
            {
                "error": (
                    "Request cannot be transcoded"
                    if lockfile_only
                    else "Request cannot be exported"
                ),
                "reasons": reasons,
            },
            status_code=HTTP_400_BAD_REQUEST,
        )


@dataclass
class SbomRequest(ResolveRequest):
    """Solve requirements or inventory explicitly selected workspace lock entries."""

    manifest: str | None = None
    manifest_filename: str | None = None

    async def locked_response(self, request: Request, output: OutputFormat) -> Response:
        """Render every selected SBOM before retaining the complete collection."""
        if self.file is None:
            error = "Provide workspace lockfile content"
        elif not self.environments or not self.platforms:
            error = (
                "Select at least one explicit environment and platform for locked SBOMs"
            )
        elif self.specs or self.channels:
            error = "Locked SBOMs do not accept extra specs or channel overrides"
        else:
            error = None
        if error:
            return Response(
                ErrorResponse(error=error), status_code=HTTP_400_BAD_REQUEST
            )

        parsed = await parse_input_for_request(
            request,
            self.file,
            self.filename,
            self.platforms,
            export_format=output.exporter.name,
            target_environments=self.environments,
            export_each=True,
            manifest_content=self.manifest,
            manifest_filename=self.manifest_filename,
        )
        if isinstance(parsed, Response):
            return parsed

        cache: ResultCache = request.app.state.result_cache
        exporter_identity = output.cache_identity()
        identity = {
            "operation": "sbom",
            "input": parsed.workspace_lock.cache_identity(),
            "output": output.exporter.name,
            "exporter": exporter_identity,
        }
        documents = []
        for exported in parsed.exported_documents:
            body = exported.content.encode("utf-8")
            identity["target"] = (
                exported.environment,
                exported.platform,
                exported.subdir,
            )
            digest = hashlib.sha256(msgspec.json.encode(identity)).hexdigest()
            response = await cache.remember(
                cache.request_key(digest),
                body,
                output.media_type,
                retain=exporter_identity is not None,
            )
            document = {
                "environment": exported.environment,
                "platform": exported.platform,
                "subdir": exported.subdir,
                "content": exported.content,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            if location := response.headers.get("Location"):
                document["location"] = location
            documents.append(document)
        return Response({"sboms": documents}, headers={"Cache-Control": "no-store"})


class SignRequest(msgspec.Struct, forbid_unknown_fields=True):
    """Identify a retained output to sign with the service identity."""

    key: str


class VerifyRequest(msgspec.Struct, forbid_unknown_fields=True):
    """Artifact bytes are represented as base64 in JSON."""

    artifact: bytes
    bundle: str
    artifact_name: str
    expected_identity: str
    expected_issuer: str


class ParseRequest(msgspec.Struct, forbid_unknown_fields=True):
    """JSON body for ``POST /parse``."""

    file: str
    filename: str | None = None
    environments: list[str] | None = None
    platforms: list[str] | None = None


class CheckLockRequest(msgspec.Struct, forbid_unknown_fields=True):
    """A complete workspace lock and its manifest for consistency checking."""

    file: str
    filename: str
    manifest: str
    manifest_filename: str


class ValidationErrorResponse(msgspec.Struct, omit_defaults=True):
    """Litestar's request validation error payload."""

    status_code: int
    detail: str
    extra: list[dict[str, str]] | None = None


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
    export_format: str | None = None,
    lockfile_only: bool = False,
    target_environments: list[str] | None = None,
    export_each: bool = False,
    manifest_content: str | None = None,
    manifest_filename: str | None = None,
    check_lock: bool = False,
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
                    export_format=export_format,
                    lockfile_only=lockfile_only,
                    target_environments=target_environments,
                    export_each=export_each,
                    manifest_content=manifest_content,
                    manifest_filename=manifest_filename,
                    check_lock=check_lock,
                ),
                limiter=capacity,
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
    workspace: WorkspaceInput | None = None,
) -> Response | tuple[bytes, str]:
    """Shared solve runner: threadpool + timeout + error sanitization.

    When *format_name* is ``None``, runs the native path
    (``solve`` → ``list[SolveResult]`` as JSON).  When set, runs the
    exporter path (``solve_environments`` → conda exporter plugin →
    string body with a format-appropriate ``Content-Type``).
    """

    timeout_s = SOLVE_TIMEOUT_S if timeout_s is None else timeout_s
    deadline = time.monotonic() + timeout_s
    workspace_args = {"workspace": workspace} if workspace is not None else {}
    try:
        capacity = request.app.state.solver_limiter
        limiter = capacity
        worker = getattr(request.app.state, "solve_worker", None)
        if worker is not None:
            with anyio.fail_after(timeout_s):
                result = await anyio.to_thread.run_sync(
                    partial(worker.solve, **workspace_args),
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
                    partial(run_solve_work, **workspace_args),
                    channels,
                    specs,
                    platforms,
                    format_name,
                    abandon_on_cancel=True,
                )
        else:
            with anyio.fail_after(timeout_s):
                result = await anyio.to_thread.run_sync(
                    partial(run_solve_in_process, **workspace_args),
                    channels,
                    specs,
                    platforms,
                    format_name,
                    deadline,
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
    except WorkspaceSolveError as exc:
        return Response(
            {
                "error": exc.error,
                "environment": exc.environment,
                "platform": exc.platform,
            },
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
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
    workspace: WorkspaceInput | None = None,
) -> list | tuple[str, str]:
    """Run the blocking solve/export path in a worker."""
    if workspace is not None:
        return workspace.solve(format_name)
    if format_name is None:
        return solve(
            channels,
            specs,
            platforms,
        )
    envs = solve_environments(channels, specs, platforms)
    return OutputFormat.named(format_name).render(envs)


def run_solve_in_process(
    channels: list[str],
    specs: list[str],
    platforms: list[str] | None,
    format_name: str | None,
    deadline: float,
    workspace: WorkspaceInput | None = None,
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
        )
        + ((workspace,) if workspace is not None else ()),
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
    if status == "workspace-error":
        raise WorkspaceSolveError(*payload)
    raise RuntimeError("Solve worker failed")


def solve_process_entrypoint(
    sender,
    channels: list[str],
    specs: list[str],
    platforms: list[str] | None,
    format_name: str | None,
    workspace: WorkspaceInput | None = None,
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
                    **({"workspace": workspace} if workspace is not None else {}),
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
    except WorkspaceSolveError as exc:
        sender.send(("workspace-error", (exc.environment, exc.platform, exc.error)))
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
    workspace: WorkspaceInput | None = None,
) -> Response:
    """Run a solve through the retained result cache."""
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
    repodata_options = workspace.repodata_options() if workspace is not None else {}
    try:
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            workspace_args = {}
            if workspace is not None:
                workspace_args["workspace_identity"] = await anyio.to_thread.run_sync(
                    workspace.cache_identity,
                    limiter=capacity,
                    abandon_on_cancel=False,
                )
            initial_repodata, solve_context = await anyio.to_thread.run_sync(
                partial(cache.capture_state, **repodata_options),
                channels,
                resolved_platforms,
                limiter=capacity,
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
                **workspace_args,
            )
            key = cache.request_key(digest)
            if retain_result and not initial_repodata.stale:
                cached_response = await cache.get_response(key)
                if cached_response is not None:
                    current_repodata = await anyio.to_thread.run_sync(
                        partial(RepodataSnapshot.capture, **repodata_options),
                        channels,
                        resolved_platforms,
                        limiter=capacity,
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
                **({"workspace": workspace} if workspace is not None else {}),
            )
            if isinstance(payload, Response):
                return payload

            # Recompute so refreshed repodata and the worker's index agree.
            repodata = await anyio.to_thread.run_sync(
                partial(RepodataSnapshot.capture, **repodata_options),
                channels,
                resolved_platforms,
                limiter=capacity,
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
                **workspace_args,
            )
            key = cache.request_key(digest)
            body, media_type = payload
            return await cache.remember(
                key,
                body,
                media_type,
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


@get(
    ["/", "/openapi.json"],
    media_type="application/vnd.oai.openapi+json",
    include_in_schema=False,
)
async def openapi_json(request: Request) -> dict[str, object]:
    """Serve the generated OpenAPI document at its stable public path."""
    return request.app.openapi_schema.to_schema()


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
    environment: FromQuery[list[str] | None] = None,
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
    data = await ResolveRequest.from_http(
        request, spec, channel, platform, filename, environment
    )
    if isinstance(data, Response):
        return data

    inputs = await data.inputs(request)
    if isinstance(inputs, Response):
        return inputs
    if isinstance(inputs, WorkspaceInput):
        try:
            inputs.validate_output(format)
        except ValueError as exc:
            return Response(
                ErrorResponse(error=str(exc)), status_code=HTTP_400_BAD_REQUEST
            )
        return await run_cached_solve(
            request,
            [spec for target in inputs.result.selected for spec in target.specs],
            list(
                dict.fromkeys(
                    channel
                    for target in inputs.result.selected
                    for channel in target.channels
                )
            ),
            list(dict.fromkeys(target.subdir for target in inputs.result.selected)),
            format_name=format,
            workspace=inputs,
        )
    return await run_cached_solve(
        request,
        inputs.specs,
        inputs.channels,
        inputs.platforms or None,
        format_name=format,
    )


@post("/export", status_code=200)
async def export_post(
    request: Request,
    spec: FromQuery[list[str] | None] = None,
    channel: FromQuery[list[str] | None] = None,
    platform: FromQuery[list[str] | None] = None,
    format: FromQuery[str | None] = None,
    filename: FromQuery[str | None] = None,
    environment: FromQuery[list[str] | None] = None,
) -> Response:
    """Export declarations or saved package selections without solving."""
    data = await ExportRequest(
        filename=filename,
        platforms=platform,
        environments=environment,
        specs=spec,
        channels=channel,
    ).read(request)
    if isinstance(data, Response):
        return data
    return await data.response(request, format)


@post("/transcode", status_code=200)
async def transcode_post(
    request: Request,
    spec: FromQuery[list[str] | None] = None,
    channel: FromQuery[list[str] | None] = None,
    platform: FromQuery[list[str] | None] = None,
    format: FromQuery[str | None] = None,
    filename: FromQuery[str | None] = None,
    environment: FromQuery[list[str] | None] = None,
) -> Response:
    """Convert supported lockfiles without solving or downloading packages."""
    data = await ExportRequest(
        filename=filename,
        platforms=platform,
        environments=environment,
        specs=spec,
        channels=channel,
    ).read(request)
    if isinstance(data, Response):
        return data
    return await data.response(request, format, lockfile_only=True)


@get("/r/{key:str}")
async def result_get(request: Request, key: FromPath[str]) -> Response:
    """Return exact retained output bytes and their media type."""
    cache: ResultCache = request.app.state.result_cache
    cached_response = await cache.get_response(
        cache.resolve_key(key),
        location=f"/r/{key}",
        immutable=True,
    )
    if cached_response is None:
        return Response(
            {"error": "Result not in cache. Submit the solve again to recompute."},
            status_code=HTTP_404_NOT_FOUND,
        )
    return cached_response


@post(
    "/check-lock",
    status_code=200,
    responses={
        200: ResponseSpec(
            data_container=WorkspaceLockCheckResult,
            description="Workspace lock consistency for every declared target",
        ),
        HTTP_400_BAD_REQUEST: ResponseSpec(
            data_container=ErrorResponse | ValidationErrorResponse,
            description="Malformed or unsupported input",
        ),
        HTTP_504_GATEWAY_TIMEOUT: ResponseSpec(
            data_container=ErrorResponse,
            description="Workspace lock checking timed out",
        ),
    },
)
async def check_lock_post(request: Request, data: CheckLockRequest) -> Response:
    """Check the whole workspace lock against the supplied manifest."""
    if request.content_type[0] != "application/json":
        return Response(
            ErrorResponse(error="POST /check-lock requires application/json"),
            status_code=HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )
    if request.query_params:
        return Response(
            ErrorResponse(error="POST /check-lock does not accept query parameters"),
            status_code=HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )
    if not all((data.file, data.filename, data.manifest, data.manifest_filename)):
        return Response(
            ErrorResponse(
                error="Provide file, filename, manifest and manifest_filename"
            ),
            status_code=HTTP_400_BAD_REQUEST,
            headers={"Cache-Control": "no-store"},
        )
    parsed = await parse_input_for_request(
        request,
        data.file,
        data.filename,
        manifest_content=data.manifest,
        manifest_filename=data.manifest_filename,
        check_lock=True,
    )
    if isinstance(parsed, Response):
        parsed.headers["Cache-Control"] = "no-store"
        return parsed
    return Response(parsed.lock_check, headers={"Cache-Control": "no-store"})


@post("/sbom", status_code=200)
async def sbom_post(request: Request, data: SbomRequest) -> Response:
    """Return CycloneDX documents from new solves or selected workspace lock records."""
    format_name = "cyclonedx-json-v1.7"
    if format_name not in OutputFormat.available():
        return Response(
            ErrorResponse(error="SBOM generation requires conda-sboms"),
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
        )
    if Path(data.filename or "").name == LOCKFILE_NAME:
        return await data.locked_response(request, OutputFormat.named(format_name))
    if data.manifest is not None or data.manifest_filename is not None:
        return Response(
            ErrorResponse(error="Manifest context requires workspace lock input"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    if not data.platforms:
        return Response(
            ErrorResponse(error="Select at least one explicit platform"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    inputs = await data.inputs(request, allow_lockfile=False)
    if isinstance(inputs, Response):
        return inputs
    documents = []
    try:
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            for platform in dict.fromkeys(inputs.platforms):
                response = await run_cached_solve(
                    request, inputs.specs, inputs.channels, [platform], format_name
                )
                if response.status_code not in (None, 200):
                    return response
                body = response.content
                if isinstance(body, str):
                    body = body.encode("utf-8")
                document = {
                    "platform": platform,
                    "content": body.decode("utf-8"),
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
                if location := response.headers.get("Location"):
                    document["location"] = location
                documents.append(document)
    except TimeoutError:
        return Response(
            ErrorResponse(error="SBOM generation exceeded the solve timeout"),
            status_code=HTTP_504_GATEWAY_TIMEOUT,
        )
    return Response({"sboms": documents}, headers={"Cache-Control": "no-store"})


@post("/sign", status_code=200)
async def sign_post(request: Request, data: SignRequest) -> Response:
    """Sign exact bytes retrieved from this deployment's output cache."""
    if not SIGSTORE_SIGNING_ENABLED:
        return Response(
            ErrorResponse(error="Output signing is disabled"),
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
        )
    if len(data.key) != 64 or any(c not in "0123456789abcdef" for c in data.key):
        return Response(
            ErrorResponse(error="Invalid result key"), status_code=HTTP_400_BAD_REQUEST
        )
    cache: ResultCache = request.app.state.result_cache
    stored = await cache.get_stored(cache.resolve_key(data.key))
    if stored is None:
        return Response(
            ErrorResponse(error="Result is no longer retained"),
            status_code=HTTP_404_NOT_FOUND,
        )
    artifact_name = f"result-{data.key}"
    service = AttestationService(
        trust_config=Path(SIGSTORE_TRUST_CONFIG) if SIGSTORE_TRUST_CONFIG else None,
        allow_public_signing=SIGSTORE_ALLOW_PUBLIC_SIGNING,
        offline=SIGSTORE_OFFLINE,
    )
    try:
        deadline = time.monotonic() + SOLVE_TIMEOUT_S
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            bundle = await anyio.to_thread.run_sync(
                partial(
                    service.run_until,
                    "sign",
                    deadline=deadline,
                    body=stored.body,
                    artifact_name=artifact_name,
                ),
                limiter=request.app.state.solver_limiter,
                abandon_on_cancel=False,
            )
    except TimeoutError:
        return Response(
            ErrorResponse(error="Signing timed out"),
            status_code=HTTP_504_GATEWAY_TIMEOUT,
        )
    except AttestationError as exc:
        return Response(
            {"error": str(exc), "code": exc.code},
            status_code=HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response(
        {
            "artifact_name": artifact_name,
            "sha256": hashlib.sha256(stored.body).hexdigest(),
            "bundle": bundle,
        },
        headers={"Cache-Control": "no-store"},
    )


@post("/verify", status_code=200)
async def verify_post(request: Request, data: VerifyRequest) -> Response:
    """Verify supplied bytes, their signature, and the expected signer pair."""
    service = AttestationService(
        trust_config=Path(SIGSTORE_TRUST_CONFIG) if SIGSTORE_TRUST_CONFIG else None,
        offline=SIGSTORE_OFFLINE,
    )
    try:
        deadline = time.monotonic() + SOLVE_TIMEOUT_S
        with anyio.fail_after(SOLVE_TIMEOUT_S):
            result = await anyio.to_thread.run_sync(
                partial(
                    service.run_until,
                    "verify",
                    deadline=deadline,
                    body=data.artifact,
                    bundle_json=data.bundle,
                    artifact_name=data.artifact_name,
                    expected_identity=data.expected_identity,
                    expected_issuer=data.expected_issuer,
                ),
                limiter=request.app.state.solver_limiter,
                abandon_on_cancel=False,
            )
    except TimeoutError:
        return Response(
            ErrorResponse(error="Verification timed out"),
            status_code=HTTP_504_GATEWAY_TIMEOUT,
        )
    except AttestationError as exc:
        return Response(
            {"error": str(exc), "code": exc.code},
            status_code=(
                HTTP_503_SERVICE_UNAVAILABLE
                if exc.code in {"provider-unavailable", "evidence-unavailable"}
                else HTTP_500_INTERNAL_SERVER_ERROR
                if exc.code in {"verification-failed", "operation-failed"}
                else HTTP_422_UNPROCESSABLE_ENTITY
            ),
        )
    return Response(result, headers={"Cache-Control": "no-store"})


@get("/capabilities")
async def capabilities() -> dict[str, bool]:
    """Report installed optional adapters and deliberate signing configuration."""
    available = AttestationService.available()
    return {
        "workspace_parse": True,
        "workspace_solve": True,
        "workspace_lock_parse": True,
        "workspace_lock_export": True,
        "workspace_lock_check": True,
        "workspace_lock_sbom": "cyclonedx-json-v1.7" in OutputFormat.available(),
        "export": True,
        "sbom": "cyclonedx-json-v1.7" in OutputFormat.available(),
        "verify": available,
        "sign": available
        and SIGSTORE_SIGNING_ENABLED
        and not SIGSTORE_OFFLINE
        and bool(SIGSTORE_TRUST_CONFIG or SIGSTORE_ALLOW_PUBLIC_SIGNING),
    }


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
    for pkg in (
        "conda-rattler-solver",
        "conda-lockfiles",
        "conda-workspaces",
        "conda-sboms",
        "conda-sigstore",
    ):
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
            data_container=ParseResult
            | WorkspaceParseResult
            | WorkspaceLockParseResult,
            description="Requirements, workspace manifests, or named locked targets",
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
    """Inspect input requirements or select manifest and lockfile targets."""
    if not data.file:
        return Response(
            ErrorResponse(error="Field 'file' is required"),
            status_code=HTTP_400_BAD_REQUEST,
        )
    parsed = await parse_input_for_request(
        request,
        data.file,
        data.filename,
        data.platforms,
        target_environments=data.environments,
    )
    if isinstance(parsed, Response):
        return parsed
    parsed_file = parsed
    if parsed_file.workspace_lock is not None:
        return Response(parsed_file.parse_result)
    if parsed_file.workspace is not None:
        for target in parsed_file.workspace.result.selected:
            if cap_error := validate_caps(
                target.specs, target.channels, [target.subdir]
            ):
                return cap_error
        return Response(parsed_file.parse_result)
    if data.platforms is not None:
        return Response(
            ErrorResponse(
                error="Platform selection requires a workspace manifest or lockfile"
            ),
            status_code=HTTP_400_BAD_REQUEST,
        )
    if cap_error := validate_caps(parsed_file.specs, parsed_file.channels, []):
        return cap_error
    return Response(parsed_file.parse_result)


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

        app.state.solver_limiter = anyio.CapacityLimiter(MAX_CONCURRENCY)
        app.state.result_cache = ResultCache(
            max_size=RESULT_CACHE_SIZE,
            max_bytes=RESULT_CACHE_MAX_MEMORY_BYTES,
            store_operations=store_operations,
        )
        if PERSISTENT_WORKER:
            app.state.solve_worker = PersistentSolveWorker(
                DEFAULT_CHANNELS,
                DEFAULT_PLATFORMS,
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


def build_cors_config(origins: list[str]) -> CORSConfig | None:
    """Return a CORS config only when origins are explicitly configured."""
    if not origins:
        return None
    return CORSConfig(allow_origins=origins)


middleware = [
    LoggingMiddlewareConfig(
        request_log_fields=("path", "method", "content_type"),
        response_log_fields=("status_code",),
    ).middleware
]
if RATE_LIMIT:
    middleware.append(RateLimitConfig(rate_limit=("minute", RATE_LIMIT)).middleware)


app = Litestar(
    route_handlers=[
        openapi_json,
        resolve_get,
        resolve_post,
        transcode_post,
        export_post,
        check_lock_post,
        sbom_post,
        sign_post,
        verify_post,
        capabilities,
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
        path="/schema",
        render_plugins=[JsonRenderPlugin()],
    ),
    lifespan=[solver_resources_lifespan],
    request_max_body_size=MAX_BODY_BYTES,
    compression_config=CompressionConfig(backend="brotli", brotli_gzip_fallback=True),
    cors_config=build_cors_config(CORS_ORIGINS),
    logging_config=LoggingConfig(
        log_exceptions="always",
        disable_stack_trace=set(range(400, 500)),
        loggers={"conda_presto": {"level": LOG_LEVEL}},
    ),
    middleware=middleware,
)
