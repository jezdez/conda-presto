"""Serialize operations for Litestar persistent stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

import anyio
from litestar.stores.base import Store


@dataclass
class StoreOperation:
    """One queued store operation and its completion state."""

    kind: Literal["get", "set"]
    key: str
    completed: anyio.Event
    value: str | bytes | None = None
    expires_in: int | timedelta | None = None
    result: bytes | None = None
    error: Exception | None = None


@dataclass
class StoreOperationCoordinator:
    """Serialize store operations without abandoning admitted work."""

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
        send_stream, receive_stream = anyio.create_memory_object_stream[StoreOperation](
            1
        )
        self.send_stream = send_stream
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self.run, receive_stream)
                try:
                    yield
                finally:
                    with anyio.CancelScope(shield=True):
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
                        else:
                            await self.store.set(
                                operation.key,
                                (
                                    operation.value
                                    if operation.value is not None
                                    else b""
                                ),
                                expires_in=operation.expires_in,
                            )
                    except Exception as exc:
                        operation.error = exc
                    finally:
                        operation.completed.set()

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
