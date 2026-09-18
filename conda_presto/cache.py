"""Public HTTP result caching."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from importlib.metadata import version as pkg_version
from pathlib import Path

import msgspec
from conda.base.context import context
from conda.core.index import Index
from conda.models.channel import Channel
from conda.models.match_spec import MatchSpec
from litestar.response import Response
from litestar.stores.base import Store
from litestar.stores.file import FileStore

from .exceptions import contains_credentials
from .exporter import ExporterCacheIdentity
from .resolve import (
    NATIVE_SUBDIR,
    RepodataSnapshot,
    configure_platform,
    platform_lock,
)
from .storage import StoreOperationCoordinator

log = logging.getLogger(__name__)

RESULT_RESPONSE_CACHE_CONTROL = "no-store"
PERMALINK_CACHE_CONTROL = "public, max-age=86400, immutable"
CACHE_ENVELOPE_VERSION = 6
RESULT_CACHE_STORE_PREFIX = "resolve-v1:"
REQUEST_CACHE_STORE_PREFIX = "request-v1:"
RESULT_CACHE_STORE_TIMEOUT_S = 2
RESULT_CACHE_STORE_MAX_AGE_S = 24 * 60 * 60
RESULT_CACHE_MAX_STORED_BYTES = 64 * 1024 * 1024
CACHE_DEPENDENCY_PACKAGES = (
    "conda-presto",
    "conda",
    "conda-rattler-solver",
    "py-rattler",
)


class StoredResult(msgspec.Struct):
    body: bytes
    media_type: str

    @property
    def digest(self) -> str:
        """Identify the exact output bytes and their media type."""
        return hashlib.sha256(self.media_type.encode() + b"\0" + self.body).hexdigest()

    @property
    def memory_size(self) -> int:
        return len(self.body) + len(self.media_type)


@dataclass(frozen=True)
class ResolveCacheContext:
    """Conda state that can change a public resolve result."""

    channel_priority: str
    use_only_tar_bz2: bool
    pinned_packages: tuple[str, ...]
    allow_cycles: bool
    add_pip_as_python_dependency: bool
    virtual_packages: tuple[tuple[str, tuple[str, ...]], ...]

    @classmethod
    def capture(cls, platforms: list[str]) -> ResolveCacheContext:
        """Capture effective conda settings and virtual packages by platform."""
        virtual_packages = []
        with platform_lock:
            for platform in platforms:
                configure_platform(platform)
                records = Index().system_packages
                virtual_packages.append(
                    (
                        platform,
                        tuple(
                            sorted(
                                json.dumps(
                                    record.dump(),
                                    sort_keys=True,
                                    separators=(",", ":"),
                                    default=str,
                                )
                                for record in records
                            )
                        ),
                    )
                )
            return cls(
                channel_priority=context.channel_priority.value,
                use_only_tar_bz2=bool(context.use_only_tar_bz2),
                pinned_packages=tuple(
                    sorted(str(MatchSpec(spec)) for spec in context.pinned_packages)
                ),
                allow_cycles=bool(context.allow_cycles),
                add_pip_as_python_dependency=bool(context.add_pip_as_python_dependency),
                virtual_packages=tuple(virtual_packages),
            )


@dataclass
class ResultCache:
    max_size: int
    max_bytes: int = 0
    entries: OrderedDict[str, StoredResult] = field(default_factory=OrderedDict)
    current_bytes: int = 0
    store_operations: StoreOperationCoordinator | None = None

    @staticmethod
    def key_for(
        specs: list[str],
        channels: list[str],
        platforms: list[str] | None,
        format_name: str | None,
        repodata: RepodataSnapshot | None = None,
        *,
        solve_context: ResolveCacheContext | None = None,
        exporter_identity: ExporterCacheIdentity | None = None,
        workspace_identity: dict | None = None,
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

        if solve_context is None:
            solve_context = ResolveCacheContext.capture(resolved_platforms)

        envelope = {
            "version": CACHE_ENVELOPE_VERSION,
            "specs": sorted(specs),
            "channels": list(channels),
            "platforms": resolved_platforms,
            "output": (
                {"kind": "native"}
                if format_name is None
                else {
                    "kind": "exporter",
                    "name": format_name,
                    "provider": exporter_identity,
                }
            ),
            "dependency_versions": versions,
            "repodata": repodata.records,
            "solve_context": asdict(solve_context),
        }
        if workspace_identity is not None:
            # Workspace identities include virtual packages for each declared target.
            del envelope["solve_context"]["virtual_packages"]
            envelope["workspace"] = workspace_identity
        body = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(body).hexdigest()

    @staticmethod
    def capture_state(
        channels: list[str],
        platforms: list[str],
        **repodata_options: bool | list[tuple[str, str]],
    ) -> tuple[RepodataSnapshot, ResolveCacheContext]:
        """Capture the blocking state used by public cache lookup."""
        return (
            RepodataSnapshot.capture(channels, platforms, **repodata_options),
            ResolveCacheContext.capture(platforms),
        )

    @staticmethod
    def channels_have_credentials(channels: list[str]) -> bool:
        """Return whether any channel contains credentials or a signed URL."""
        for value in channels:
            if contains_credentials(value):
                return True
            try:
                expanded = context.custom_multichannels.get(value)
                channel_values = expanded if expanded is not None else (Channel(value),)
                for channel in channel_values:
                    public_urls = channel.urls(
                        with_credentials=False,
                        subdirs=("noarch",),
                    )
                    credentialed_urls = channel.urls(
                        with_credentials=True,
                        subdirs=("noarch",),
                    )
                    if (
                        channel.auth
                        or channel.token
                        or public_urls != credentialed_urls
                        or any(contains_credentials(url) for url in credentialed_urls)
                    ):
                        return True
            except Exception:
                return True
        return False

    @staticmethod
    def specs_have_credentials(specs: list[str]) -> bool:
        """Return whether any spec selects a credentialed channel."""
        for value in specs:
            if contains_credentials(value):
                return True
            try:
                channel = MatchSpec(value).get_exact_value("channel")
            except Exception:
                return True
            if channel is not None and ResultCache.channels_have_credentials(
                [getattr(channel, "canonical_name", str(channel))]
            ):
                return True
        return False

    @staticmethod
    def resolve_key(key: str) -> str:
        """Return the storage key for an immutable output digest."""
        return f"{RESULT_CACHE_STORE_PREFIX}{key}"

    @staticmethod
    def request_key(key: str) -> str:
        """Return the lookup key for canonical solve inputs."""
        return f"{REQUEST_CACHE_STORE_PREFIX}{key}"

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

    def remember_memory(self, key: str, stored: StoredResult) -> bool:
        if self.stored_entry_has_credentials(stored) or (
            self.max_bytes > 0 and stored.memory_size > self.max_bytes
        ):
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

    @staticmethod
    def stored_entry_has_credentials(stored: StoredResult) -> bool:
        """Return whether a produced result contains detected credentials."""
        try:
            value = msgspec.json.decode(stored.body)
        except msgspec.DecodeError:
            value = stored.body
        return contains_credentials(value)

    async def get_stored(self, key: str) -> StoredResult | None:
        """Load a result from memory or persistent storage."""
        stored = self.entries.get(key)
        if stored is not None:
            if key.startswith(RESULT_CACHE_STORE_PREFIX) and (
                key != self.resolve_key(stored.digest)
            ):
                log.warning("Ignoring in-memory result with mismatched artifact digest")
                await self.discard(key)
                return None
            if self.stored_entry_has_credentials(stored):
                log.warning("Ignoring in-memory result cache entry with credentials")
                await self.discard(key)
                return None
            self.entries.move_to_end(key)
            return stored
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

        if len(stored_payload) > RESULT_CACHE_MAX_STORED_BYTES:
            log.warning("Ignoring oversized persistent result cache entry")
            await self.delete_persistent(key)
            return None

        try:
            stored = msgspec.msgpack.decode(stored_payload, type=StoredResult)
        except (msgspec.DecodeError, msgspec.ValidationError):
            log.warning("Ignoring corrupt persistent result cache entry")
            await self.delete_persistent(key)
            return None
        if key.startswith(RESULT_CACHE_STORE_PREFIX) and (
            key != self.resolve_key(stored.digest)
        ):
            log.warning("Ignoring persistent result with mismatched artifact digest")
            await self.delete_persistent(key)
            return None
        if self.stored_entry_has_credentials(stored):
            log.warning("Ignoring persistent result cache entry with credentials")
            await self.delete_persistent(key)
            return None

        current = self.entries.get(key)
        if current is not None:
            self.entries.move_to_end(key)
            return current
        self.remember_memory(key, stored)
        return stored

    async def get_response(
        self,
        key: str,
        *,
        location: str | None = None,
        immutable: bool = False,
    ) -> Response | None:
        stored = await self.get_stored(key)
        if stored is None:
            return None
        digest = stored.digest
        retained = immutable or await self.store_entry(self.resolve_key(digest), stored)
        return self.response_for(
            stored,
            location if immutable and location is not None else f"/r/{digest}",
            immutable=immutable,
            retained=retained,
        )

    async def delete_persistent(self, key: str) -> None:
        """Remove one invalid persistent entry on a best-effort basis."""
        if self.store_operations is None:
            return
        try:
            completed = await self.store_operations.delete(
                key,
                timeout_s=RESULT_CACHE_STORE_TIMEOUT_S,
            )
            if not completed:
                log.warning("Persistent result cache deletion timed out")
        except Exception:
            log.warning("Persistent result cache deletion failed")

    async def discard(self, key: str) -> None:
        """Remove one cache entry from memory and persistent storage."""
        if stored := self.entries.pop(key, None):
            self.current_bytes -= stored.memory_size
        await self.delete_persistent(key)

    async def store_entry(self, key: str, stored: StoredResult) -> bool:
        """Retain a result only after its configured store accepts it."""
        if self.stored_entry_has_credentials(stored):
            log.warning("Not retaining result cache entry with credentials")
            await self.discard(key)
            return False
        if self.store_operations is None:
            return self.remember_memory(key, stored)

        # Failed shared-store publications must not become local cache hits
        # that advertise a result URL unavailable to another replica.
        if previous := self.entries.pop(key, None):
            self.current_bytes -= previous.memory_size
        payload = msgspec.msgpack.encode(stored)
        if len(payload) > RESULT_CACHE_MAX_STORED_BYTES:
            log.warning("Persistent result cache entry exceeds the size limit")
            return False
        try:
            completed = await self.store_operations.set(
                key,
                payload,
                timeout_s=RESULT_CACHE_STORE_TIMEOUT_S,
                expires_in=RESULT_CACHE_STORE_MAX_AGE_S,
            )
            if not completed:
                log.warning("Persistent result cache write timed out")
                return False
        except Exception:
            log.warning("Persistent result cache write failed")
            return False
        self.remember_memory(key, stored)
        return True

    async def remember(
        self,
        key: str,
        body: bytes,
        media_type: str,
        *,
        retain: bool = True,
    ) -> Response:
        stored = StoredResult(body=body, media_type=media_type)
        digest = stored.digest
        if not retain:
            return self.response_for(stored, f"/r/{digest}", retained=False)
        await self.store_entry(key, stored)
        retained = await self.store_entry(self.resolve_key(digest), stored)
        return self.response_for(stored, f"/r/{digest}", retained=retained)

    @staticmethod
    def response_for(
        stored: StoredResult,
        location: str,
        *,
        immutable: bool = False,
        retained: bool = True,
    ) -> Response:
        headers = {
            "Cache-Control": (
                PERMALINK_CACHE_CONTROL if immutable else RESULT_RESPONSE_CACHE_CONTROL
            )
        }
        if retained:
            headers["Location"] = location
        return Response(
            stored.body,
            media_type=stored.media_type,
            headers=headers,
        )
