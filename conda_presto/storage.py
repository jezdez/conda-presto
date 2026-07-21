"""Serialize operations for Litestar persistent stores."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

import anyio
from litestar.stores.base import Store
from litestar.stores.file import FileStore

log = logging.getLogger(__name__)

FILE_STORE_CLEANUP_INTERVAL_S = 60 * 60
FILE_STORE_CLEANUP_TIMEOUT_S = 60


@dataclass
class StoreOperation:
    """One queued store operation and its completion state."""

    kind: Literal["cleanup", "delete", "get", "set"]
    key: str
    completed: anyio.Event
    value: str | bytes | None = None
    expires_in: int | timedelta | None = None
    result: bytes | None = None
    error: Exception | None = None


@dataclass
class StoreOperationCoordinator:
    """Serialize access to a configured, trusted persistent store.

    Store credentials are the write authority. Payload validation protects
    against corruption, but an integrity digest without a separate secret
    would not protect against a caller that can already write to the store.
    """

    store: Store
    send_stream: anyio.abc.ObjectSendStream[StoreOperation] | None = field(
        default=None,
        init=False,
    )

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Run the store-operation consumer for this lifespan."""
        if self.send_stream is not None:
            raise RuntimeError("Store operation coordinator is already running")
        if isinstance(self.store, FileStore):
            await self.store.path.mkdir(parents=True, exist_ok=True)
            try:
                await self.expire_file_entries()
            except Exception:
                log.warning("File result-cache cleanup failed")
        send_stream, receive_stream = anyio.create_memory_object_stream[StoreOperation](
            1
        )
        self.send_stream = send_stream
        stop_cleanup = anyio.Event()
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self.run, receive_stream)
                if isinstance(self.store, FileStore):
                    tasks.start_soon(self.cleanup_expired, stop_cleanup)
                try:
                    yield
                finally:
                    with anyio.CancelScope(shield=True):
                        stop_cleanup.set()
                        await send_stream.aclose()
        finally:
            self.send_stream = None

    async def run(
        self,
        receive_stream: anyio.abc.ObjectReceiveStream[StoreOperation],
    ) -> None:
        """Apply queued operations in submission order."""
        with anyio.CancelScope(shield=True):
            async with receive_stream:
                async for operation in receive_stream:
                    try:
                        if operation.kind == "get":
                            operation.result = await self.store.get(operation.key)
                        elif operation.kind == "delete":
                            await self.store.delete(operation.key)
                        elif operation.kind == "cleanup":
                            await self.expire_file_entries()
                        else:
                            value = (
                                operation.value if operation.value is not None else b""
                            )
                            await self.store.set(
                                operation.key,
                                value,
                                expires_in=operation.expires_in,
                            )
                            if isinstance(self.store, FileStore):
                                expected = (
                                    value.encode() if isinstance(value, str) else value
                                )
                                try:
                                    if await self.store.get(operation.key) != expected:
                                        raise OSError("File result-cache write failed")
                                except Exception:
                                    try:
                                        await self.store.delete(operation.key)
                                    except Exception:
                                        log.warning(
                                            "Invalid FileStore entry cleanup failed"
                                        )
                                    raise
                    except Exception as exc:
                        operation.error = exc
                    finally:
                        operation.completed.set()

    async def expire_file_entries(self) -> None:
        """Delete expired files, clearing the disposable cache if corrupt."""
        if not isinstance(self.store, FileStore):
            return
        try:
            await self.store.delete_expired()
        except Exception:
            log.warning("Corrupt FileStore detected, clearing the result cache")
            await self.store.delete_all()

    async def cleanup_expired(self, stop: anyio.Event) -> None:
        """Periodically remove expired FileStore entries through the queue."""
        while True:
            with anyio.move_on_after(FILE_STORE_CLEANUP_INTERVAL_S):
                await stop.wait()
            if stop.is_set():
                return
            try:
                completed = await self.submit(
                    StoreOperation(kind="cleanup", key="", completed=anyio.Event()),
                    FILE_STORE_CLEANUP_TIMEOUT_S,
                )
                if not completed:
                    log.warning("File result-cache cleanup timed out")
            except Exception:
                log.warning("File result-cache cleanup failed")

    async def set(
        self,
        key: str,
        value: str | bytes,
        *,
        timeout_s: float,
        expires_in: int | timedelta | None = None,
    ) -> bool:
        """Queue a set and report whether it completed before the deadline."""
        return await self.submit(
            StoreOperation(
                kind="set",
                key=key,
                value=value,
                expires_in=expires_in,
                completed=anyio.Event(),
            ),
            timeout_s,
        )

    async def get(self, key: str, *, timeout_s: float) -> tuple[bool, bytes | None]:
        """Queue a get and report its result before the caller deadline."""
        operation = StoreOperation(kind="get", key=key, completed=anyio.Event())
        completed = await self.submit(operation, timeout_s)
        return completed, operation.result

    async def delete(self, key: str, *, timeout_s: float) -> bool:
        """Queue a deletion and report whether it met the caller deadline."""
        return await self.submit(
            StoreOperation(kind="delete", key=key, completed=anyio.Event()),
            timeout_s,
        )

    async def submit(self, operation: StoreOperation, timeout_s: float) -> bool:
        """Submit one operation to the store queue."""
        if self.send_stream is None:
            raise RuntimeError("Store operation coordinator is not running")
        with anyio.move_on_after(max(0.0, timeout_s)) as scope:
            await self.send_stream.send(operation)
            await operation.completed.wait()
        if scope.cancel_called:
            return False
        if operation.error is not None:
            raise operation.error
        return True
