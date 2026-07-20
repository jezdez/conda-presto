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
from litestar import Request
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
from .worker import PersistentSolveWorker

log = logging.getLogger(__name__)

RESULT_CACHE_CONTROL = "public, max-age=86400, immutable"
DEFAULT_RESOLVE_FORMAT = "conda-presto-json-v1"
CACHE_ENVELOPE_VERSION = 3
RESULT_CACHE_STORE_NAME = "result_cache"
RESULT_CACHE_STORE_PREFIX = "resolve-v1:"
SOLVER_CACHE_STORE_PREFIX = "solver-v1:"
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
    "publication-rejected",
    "solver-error",
]


@dataclass
class ResultCache:
    max_size: int
    max_bytes: int = 0
    store_name: str | None = None
    entries: OrderedDict[str, StoredCacheEntry] = field(default_factory=OrderedDict)
    current_bytes: int = 0
    solver_publication_lock: anyio.Lock = field(default_factory=anyio.Lock)

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

    def store_from(self, request: Request) -> Store | None:
        """Return the configured persistent store, if enabled."""
        if self.store_name is None:
            return None
        return request.app.stores.get(self.store_name)

    @staticmethod
    def resolve_key(key: str) -> str:
        """Return the private storage key for a public resolve digest."""
        return f"{RESULT_CACHE_STORE_PREFIX}{key}"

    @staticmethod
    def solver_key(key: str) -> str:
        """Return the private storage key for a solver cache entry."""
        return f"{SOLVER_CACHE_STORE_PREFIX}{key}"

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
        store: Store | None = None,
    ) -> StoredCacheEntry | None:
        """Load one typed cache entry from memory or persistent storage."""
        stored = self.entries.get(key)
        if isinstance(stored, entry_type):
            self.entries.move_to_end(key)
            return stored
        if stored is not None:
            self.current_bytes -= stored.memory_size
            del self.entries[key]

        if store is None:
            return None

        try:
            stored_payload = await store.get(key)
        except Exception:
            log.warning("Persistent result cache read failed", exc_info=True)
            return None
        if stored_payload is None:
            return None

        try:
            stored = msgspec.msgpack.decode(stored_payload, type=entry_type)
        except (msgspec.DecodeError, msgspec.ValidationError):
            log.warning("Ignoring corrupt persistent result cache entry for %s", key)
            try:
                await store.delete(key)
            except Exception:
                log.warning(
                    "Persistent result cache cleanup failed for %s",
                    key,
                    exc_info=True,
                )
            return None

        self.remember_memory(key, stored)
        return stored

    async def get_response(
        self,
        key: str,
        store: Store | None = None,
        *,
        location: str,
    ) -> Response | None:
        stored = await self.get_stored(key, StoredResult, store)
        if not isinstance(stored, StoredResult):
            return None
        return self.response_for(stored, location)

    async def get_solver_result(
        self,
        request: PrestoSolveRequest,
        store: Store | None = None,
    ) -> StoredSolverResult | None:
        """Return a solver result only after a post-read freshness check."""
        async with self.solver_publication_lock:
            stored = await self.get_stored(
                self.solver_key(request.cache_key()),
                StoredSolverResult,
                store,
            )
            if not isinstance(stored, StoredSolverResult):
                return None
            try:
                current = await anyio.to_thread.run_sync(
                    request.repodata_snapshot,
                    abandon_on_cancel=True,
                )
            except Exception:
                log.warning("Presto solver cache metadata unavailable during lookup")
                return None
            return stored if stored.matches(current) else None

    async def store_entry(
        self,
        key: str,
        stored: StoredCacheEntry,
        store: Store | None = None,
    ) -> bool:
        """Retain one cache entry and return whether a cache accepted it."""
        retained = self.remember_memory(key, stored)
        if store is not None:
            try:
                await store.set(key, msgspec.msgpack.encode(stored))
                retained = True
            except Exception:
                log.warning("Persistent result cache write failed", exc_info=True)
        return retained

    async def remember(
        self,
        key: str,
        body: bytes,
        media_type: str,
        store: Store | None = None,
        *,
        location: str,
    ) -> Response:
        stored = StoredResult(body=body, media_type=media_type)
        retained = await self.store_entry(key, stored, store)
        if retained:
            return self.response_for(stored, location)
        return Response(stored.body, media_type=stored.media_type)

    async def publish_solver(
        self,
        request: PrestoSolveRequest,
        outcome: PrestoSolveOutcome,
        store: Store | None = None,
    ) -> tuple[StoredSolverResult | None, SolverCacheDisposition]:
        """Store a worker result unless the current entry already matches."""
        async with self.solver_publication_lock:
            existing = await self.get_stored(
                self.solver_key(request.cache_key()),
                StoredSolverResult,
                store,
            )
            try:
                current = await anyio.to_thread.run_sync(
                    request.repodata_snapshot,
                    abandon_on_cancel=True,
                )
            except Exception:
                log.warning("Presto solver cache metadata unavailable after solve")
                return None, "publication-rejected"
            if isinstance(existing, StoredSolverResult) and existing.matches(current):
                return existing, "already-current"
            if not outcome.is_cacheable_with(current):
                return None, "publication-rejected"
            stored = StoredSolverResult(
                response=outcome.result,
                metadata_used=outcome.metadata_used,
            )
            retained = await self.store_entry(
                self.solver_key(request.cache_key()),
                stored,
                store,
            )
        return stored, "published" if retained else "not-retained"

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


@dataclass
class SolverResultService:
    """Own solver cache lookup, worker execution, and result storage."""

    cache: ResultCache
    store: Store | None = None

    async def probe(self, request: PrestoSolveRequest) -> SolverServiceResult | None:
        """Return a cached result if its repodata markers still match."""
        stored = await self.cache.get_solver_result(request, self.store)
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
        )
        if isinstance(outcome.result, PrestoSolveError):
            return SolverServiceResult(
                result=outcome.result,
                disposition="solver-error",
            )
        stored, disposition = await self.cache.publish_solver(
            request,
            outcome,
            self.store,
        )
        return SolverServiceResult(
            result=stored.response if stored is not None else outcome.result,
            disposition=disposition,
        )
