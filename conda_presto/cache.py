"""Shared HTTP and solver result caching."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Literal

import anyio
import msgspec
from litestar.response import Response
from litestar.stores.base import Store
from litestar.stores.file import FileStore

from .resolve import NATIVE_SUBDIR, VIRTUAL_PACKAGES, RepodataSnapshot
from .solver import (
    PrestoSolveError,
    PrestoSolveOutcome,
    PrestoSolveRequest,
    PrestoSolveResponse,
)
from .storage import StoreOperationCoordinator
from .worker import PersistentSolveWorker

log = logging.getLogger(__name__)

RESULT_CACHE_CONTROL = "public, max-age=86400, immutable"
DEFAULT_RESOLVE_FORMAT = "conda-presto-json-v1"
CACHE_ENVELOPE_VERSION = 3
RESULT_CACHE_STORE_PREFIX = "resolve-v1:"
SOLVER_CACHE_STORE_PREFIX = "solver-v1:"
RESULT_CACHE_STORE_TIMEOUT_S = 2
CACHE_DEPENDENCY_PACKAGES = (
    "conda-presto",
    "conda",
    "conda-rattler-solver",
    "py-rattler",
    "conda-lockfiles",
)


class StoredResult(msgspec.Struct):
    body: bytes
    media_type: str

    @property
    def memory_size(self) -> int:
        return len(self.body) + len(self.media_type)


class StoredSolverResult(msgspec.Struct):
    """A cached solver response and its repodata cache-file markers."""

    response: PrestoSolveResponse
    metadata_used: RepodataSnapshot

    @property
    def memory_size(self) -> int:
        return len(msgspec.msgpack.encode(self))

    def matches(self, repodata: RepodataSnapshot) -> bool:
        """Return whether current repodata markers match this entry."""
        return not repodata.stale and self.metadata_used.records == repodata.records


StoredCacheEntry = StoredResult | StoredSolverResult
SolverCacheDisposition = Literal[
    "cache-hit",
    "published",
    "already-current",
    "not-retained",
    "persistent-failed",
    "publication-rejected",
    "solver-error",
]


@dataclass(frozen=True)
class CacheRetention:
    """Result of retaining one entry in memory and optional storage."""

    retained: bool
    persistent_failed: bool = False


@dataclass(frozen=True)
class SolverServiceProbe:
    """A cache result and the current repodata observed during its lookup."""

    cached: bool
    current: RepodataSnapshot | None
    persistence_failed: bool = False


@dataclass
class ResultCache:
    max_size: int
    max_bytes: int = 0
    entries: OrderedDict[str, StoredCacheEntry] = field(default_factory=OrderedDict)
    current_bytes: int = 0
    solver_publication_lock: anyio.Lock = field(default_factory=anyio.Lock)
    store_operations: StoreOperationCoordinator | None = None

    @staticmethod
    def key_for(
        specs: list[str],
        channels: list[str],
        platforms: list[str] | None,
        format_name: str | None,
        repodata: RepodataSnapshot | None = None,
    ) -> str:
        """Return the SHA-256 key for a canonical resolve request."""
        resolved_platforms = list(platforms or [NATIVE_SUBDIR])
        versions: dict[str, str] = {}
        for package in CACHE_DEPENDENCY_PACKAGES:
            try:
                versions[package] = pkg_version(package)
            except Exception:
                versions[package] = "unknown"

        if repodata is None:
            repodata = RepodataSnapshot.capture(channels, resolved_platforms)

        envelope = {
            "version": CACHE_ENVELOPE_VERSION,
            "specs": sorted(specs),
            "channels": list(channels),
            "platforms": resolved_platforms,
            "format": format_name or DEFAULT_RESOLVE_FORMAT,
            "dependency_versions": versions,
            "virtual_packages": VIRTUAL_PACKAGES,
            "repodata": repodata.records,
        }
        body = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def resolve_key(key: str) -> str:
        """Return the private storage key for a public resolve digest."""
        return f"{RESULT_CACHE_STORE_PREFIX}{key}"

    @staticmethod
    def solver_key(key: str) -> str:
        """Return the private storage key for a solver cache entry."""
        return f"{SOLVER_CACHE_STORE_PREFIX}{key}"

    @staticmethod
    def store_for_config(
        backend: str,
        cache_dir: str | None,
        redis_url: str | None,
        redis_namespace: str,
    ) -> Store | None:
        if backend == "memory":
            return None
        if backend == "file":
            if cache_dir is None:
                raise ValueError(
                    "CONDA_PRESTO_RESULT_CACHE_DIR is required "
                    "when CONDA_PRESTO_RESULT_CACHE_BACKEND=file"
                )
            return FileStore(Path(cache_dir), create_directories=True)
        if backend == "redis":
            try:
                from litestar.stores.redis import RedisStore
                from redis.asyncio import Redis
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Redis result cache requires the redis-py package. "
                    "Install conda-presto with the redis extra or use "
                    "the Pixi redis environment."
                ) from exc
            return RedisStore(
                redis=Redis.from_url(
                    redis_url or "redis://localhost:6379/0",
                    socket_connect_timeout=RESULT_CACHE_STORE_TIMEOUT_S,
                    socket_timeout=RESULT_CACHE_STORE_TIMEOUT_S,
                ),
                namespace=redis_namespace,
                handle_client_shutdown=True,
            )
        raise ValueError(f"Unsupported result cache backend: {backend}")

    def remember_memory(self, key: str, stored: StoredCacheEntry) -> bool:
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

    async def get_stored(
        self,
        key: str,
        entry_type: type[StoredResult] | type[StoredSolverResult],
    ) -> StoredCacheEntry | None:
        """Load one typed cache entry from memory or persistent storage."""
        stored = self.entries.get(key)
        if isinstance(stored, entry_type):
            self.entries.move_to_end(key)
            return stored
        if stored is not None:
            self.current_bytes -= stored.memory_size
            del self.entries[key]

        if self.store_operations is None:
            return None

        try:
            completed, stored_payload = await self.store_operations.get(
                key,
                timeout_s=RESULT_CACHE_STORE_TIMEOUT_S,
            )
            if not completed:
                log.warning("Persistent result cache read timed out")
                return None
        except Exception:
            log.warning("Persistent result cache read failed")
            return None
        if stored_payload is None:
            return None

        try:
            stored = msgspec.msgpack.decode(stored_payload, type=entry_type)
        except (msgspec.DecodeError, msgspec.ValidationError):
            log.warning("Ignoring corrupt persistent result cache entry")
            return None

        current = self.entries.get(key)
        if isinstance(current, entry_type):
            self.entries.move_to_end(key)
            return current
        if current is not None:
            self.current_bytes -= current.memory_size
            del self.entries[key]
        self.remember_memory(key, stored)
        return stored

    async def get_response(
        self,
        key: str,
        *,
        location: str,
    ) -> Response | None:
        stored = await self.get_stored(key, StoredResult)
        if not isinstance(stored, StoredResult):
            return None
        return self.response_for(stored, location)

    async def get_solver_result(
        self,
        request: PrestoSolveRequest,
        *,
        thread_limiter: anyio.CapacityLimiter | None = None,
    ) -> StoredSolverResult | None:
        """Return a solver result only after a post-read freshness check."""
        async with self.solver_publication_lock:
            stored = await self.get_stored(
                self.solver_key(request.cache_key()),
                StoredSolverResult,
            )
            if not isinstance(stored, StoredSolverResult):
                return None
            try:
                current = await anyio.to_thread.run_sync(
                    request.repodata_snapshot,
                    abandon_on_cancel=True,
                    limiter=thread_limiter,
                )
            except Exception:
                log.warning("Presto solver cache metadata unavailable during lookup")
                return None
            return stored if stored.matches(current) else None

    async def inspect_solver_result(
        self,
        request: PrestoSolveRequest,
        *,
        thread_limiter: anyio.CapacityLimiter | None = None,
        require_persistent: bool = False,
    ) -> SolverServiceProbe:
        """Inspect one solver cache entry and its current metadata."""
        async with self.solver_publication_lock:
            key = self.solver_key(request.cache_key())
            stored = await self.get_stored(key, StoredSolverResult)
            try:
                current = await anyio.to_thread.run_sync(
                    request.repodata_snapshot,
                    abandon_on_cancel=True,
                    limiter=thread_limiter,
                )
            except Exception:
                log.warning("Presto solver cache metadata unavailable during lookup")
                return SolverServiceProbe(cached=False, current=None)
            if not isinstance(stored, StoredSolverResult) or not stored.matches(
                current
            ):
                return SolverServiceProbe(cached=False, current=current)
            if require_persistent and self.store_operations is not None:
                retention = await self.store_entry(key, stored)
                if retention.persistent_failed:
                    return SolverServiceProbe(
                        cached=False,
                        current=current,
                        persistence_failed=True,
                    )
            return SolverServiceProbe(cached=True, current=current)

    async def store_entry(
        self,
        key: str,
        stored: StoredCacheEntry,
    ) -> CacheRetention:
        """Retain one cache entry and report optional storage failure."""
        retained = self.remember_memory(key, stored)
        if self.store_operations is not None:
            try:
                completed = await self.store_operations.set(
                    key,
                    msgspec.msgpack.encode(stored),
                    timeout_s=RESULT_CACHE_STORE_TIMEOUT_S,
                )
                if not completed:
                    log.warning("Persistent result cache write timed out")
                    return CacheRetention(retained, persistent_failed=True)
                retained = True
            except Exception:
                log.warning("Persistent result cache write failed")
                return CacheRetention(retained, persistent_failed=True)
        return CacheRetention(retained)

    async def remember(
        self,
        key: str,
        body: bytes,
        media_type: str,
        *,
        location: str,
    ) -> Response:
        stored = StoredResult(body=body, media_type=media_type)
        retention = await self.store_entry(key, stored)
        if retention.retained:
            return self.response_for(stored, location)
        return Response(stored.body, media_type=stored.media_type)

    async def publish_solver(
        self,
        request: PrestoSolveRequest,
        outcome: PrestoSolveOutcome,
        *,
        thread_limiter: anyio.CapacityLimiter | None = None,
        require_persistent: bool = False,
    ) -> tuple[StoredSolverResult | None, SolverCacheDisposition]:
        """Store a worker result unless the current entry already matches."""
        async with self.solver_publication_lock:
            key = self.solver_key(request.cache_key())
            existing = await self.get_stored(key, StoredSolverResult)
            try:
                current = await anyio.to_thread.run_sync(
                    request.repodata_snapshot,
                    abandon_on_cancel=True,
                    limiter=thread_limiter,
                )
            except Exception:
                log.warning("Presto solver cache metadata unavailable after solve")
                return None, "publication-rejected"
            if isinstance(existing, StoredSolverResult) and existing.matches(current):
                if require_persistent and self.store_operations is not None:
                    retention = await self.store_entry(key, existing)
                    if retention.persistent_failed:
                        return existing, "persistent-failed"
                return existing, "already-current"
            if not outcome.is_cacheable_with(current):
                return None, "publication-rejected"
            stored = StoredSolverResult(
                response=outcome.result,
                metadata_used=outcome.metadata_used,
            )
            retention = await self.store_entry(key, stored)
            if require_persistent and retention.persistent_failed:
                return stored, "persistent-failed"
        return stored, "published" if retention.retained else "not-retained"

    @staticmethod
    def response_for(
        stored: StoredResult,
        location: str,
    ) -> Response:
        return Response(
            stored.body,
            media_type=stored.media_type,
            headers={"Location": location, "Cache-Control": RESULT_CACHE_CONTROL},
        )


@dataclass(frozen=True)
class SolverServiceResult:
    """One typed solver result and how the cache handled it."""

    result: PrestoSolveResponse | PrestoSolveError
    disposition: SolverCacheDisposition

    @property
    def should_record_for_warming(self) -> bool:
        """Return whether this result should be recorded for cache warming."""
        return self.disposition in {"cache-hit", "published", "already-current"}


@dataclass
class SolverResultService:
    """Own solver cache lookup, worker execution, and result storage."""

    cache: ResultCache
    thread_limiter: anyio.CapacityLimiter | None = None
    require_persistent: bool = False

    async def inspect(self, request: PrestoSolveRequest) -> SolverServiceProbe:
        """Return one cache inspection for background scheduling decisions."""
        return await self.cache.inspect_solver_result(
            request,
            thread_limiter=self.thread_limiter,
            require_persistent=self.require_persistent,
        )

    async def probe(self, request: PrestoSolveRequest) -> SolverServiceResult | None:
        """Return a cached result if its repodata markers still match."""
        stored = await self.cache.get_solver_result(
            request,
            thread_limiter=self.thread_limiter,
        )
        if stored is None:
            return None
        return SolverServiceResult(
            result=stored.response,
            disposition="cache-hit",
        )

    async def resolve(
        self,
        request: PrestoSolveRequest,
        worker: PersistentSolveWorker,
        deadline: float,
    ) -> SolverServiceResult:
        """Return a current cache hit or solve and publish one final state."""
        if cached := await self.probe(request):
            return cached
        outcome = await anyio.to_thread.run_sync(
            worker.solve_final_state,
            request,
            deadline,
            abandon_on_cancel=True,
            limiter=self.thread_limiter,
        )
        if isinstance(outcome.result, PrestoSolveError):
            return SolverServiceResult(
                result=outcome.result,
                disposition="solver-error",
            )
        stored, disposition = await self.cache.publish_solver(
            request,
            outcome,
            thread_limiter=self.thread_limiter,
            require_persistent=self.require_persistent,
        )
        return SolverServiceResult(
            result=stored.response if stored is not None else outcome.result,
            disposition=disposition,
        )
