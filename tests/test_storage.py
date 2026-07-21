"""Tests for ordered persistent-store operations."""

from __future__ import annotations

from datetime import timedelta

import anyio
import pytest
from litestar.stores.file import FileStore

import conda_presto.storage as storage_module
from conda_presto.storage import StoreOperationCoordinator


class RecordingStore:
    def __init__(self, initial: bytes | None = None) -> None:
        self.value = initial
        self.writes: list[bytes] = []
        self.started = anyio.Event()
        self.release = anyio.Event()
        self.block_next = True

    async def set(
        self,
        _key: str,
        value: str | bytes,
        expires_in: int | timedelta | None = None,
    ) -> None:
        del expires_in
        if self.block_next:
            self.block_next = False
            self.started.set()
            await self.release.wait()
        self.value = value.encode() if isinstance(value, str) else value
        self.writes.append(self.value)


@pytest.mark.anyio
async def test_timed_out_set_cannot_overtake_a_newer_value():
    store = RecordingStore()
    coordinator = StoreOperationCoordinator(store)

    async with coordinator.lifespan():
        assert not await coordinator.set("key", b"old", timeout_s=0.01)
        await store.started.wait()
        results = []

        async def set_new_value() -> None:
            results.append(await coordinator.set("key", b"new", timeout_s=1))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(set_new_value)
            await anyio.lowlevel.checkpoint()
            store.release.set()

    assert results == [True]
    assert store.writes == [b"old", b"new"]
    assert store.value == b"new"


@pytest.mark.anyio
async def test_store_operation_errors_reach_the_waiting_caller():
    class FailingStore:
        async def set(self, _key, _value, expires_in=None):
            raise OSError("store unavailable")

    coordinator = StoreOperationCoordinator(FailingStore())

    async with coordinator.lifespan():
        with pytest.raises(OSError, match="store unavailable"):
            await coordinator.set("key", b"value", timeout_s=1)


@pytest.mark.anyio
async def test_store_operation_coordinator_deletes_entries():
    class Store:
        value = b"value"

        async def delete(self, _key):
            self.value = None

    store = Store()
    coordinator = StoreOperationCoordinator(store)

    async with coordinator.lifespan():
        assert await coordinator.delete("key", timeout_s=1)

    assert store.value is None


@pytest.mark.anyio
async def test_file_store_expired_entries_are_removed_at_startup(tmp_path):
    store = FileStore(tmp_path / "cache", create_directories=True)
    await store.set("expired", b"value", expires_in=-1)
    assert [file async for file in store.path.iterdir()]

    coordinator = StoreOperationCoordinator(store)
    async with coordinator.lifespan():
        pass

    assert not [file async for file in store.path.iterdir()]


@pytest.mark.anyio
async def test_file_store_expired_entries_are_removed_while_running(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(storage_module, "FILE_STORE_CLEANUP_INTERVAL_S", 0.01)
    store = FileStore(tmp_path / "cache", create_directories=True)
    coordinator = StoreOperationCoordinator(store)

    async with coordinator.lifespan():
        assert await coordinator.set(
            "expired",
            b"value",
            timeout_s=1,
            expires_in=timedelta(milliseconds=20),
        )
        await anyio.sleep(0.03)
        with anyio.fail_after(1):
            while [file async for file in store.path.iterdir()]:
                await anyio.sleep(0.01)

    assert not [file async for file in store.path.iterdir()]


@pytest.mark.anyio
async def test_corrupt_file_store_entry_does_not_block_startup(tmp_path):
    store = FileStore(tmp_path / "cache", create_directories=True)
    await store.path.mkdir(parents=True)
    corrupt = store.path / "corrupt"
    await corrupt.write_bytes(b"corrupt")

    coordinator = StoreOperationCoordinator(store)
    async with coordinator.lifespan():
        pass

    assert not await corrupt.exists()


@pytest.mark.anyio
async def test_file_store_write_is_verified(tmp_path):
    class DroppingFileStore(FileStore):
        async def set(self, key, value, expires_in=None):
            pass

    store = DroppingFileStore(tmp_path / "cache", create_directories=True)
    coordinator = StoreOperationCoordinator(store)

    async with coordinator.lifespan():
        with pytest.raises(OSError, match="write failed"):
            await coordinator.set("key", b"value", timeout_s=1)

    assert await store.get("key") is None


@pytest.mark.anyio
async def test_shutdown_drains_active_and_queued_operations():
    store = RecordingStore()
    coordinator = StoreOperationCoordinator(store)
    owner_leaving = anyio.Event()
    owner_finished = anyio.Event()

    async def own_coordinator() -> None:
        async with coordinator.lifespan():
            assert not await coordinator.set("key", b"old", timeout_s=0.01)
            assert not await coordinator.set("key", b"new", timeout_s=0.01)
            owner_leaving.set()
        owner_finished.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(own_coordinator)
        await owner_leaving.wait()
        assert not owner_finished.is_set()
        store.release.set()

    assert owner_finished.is_set()
    assert store.value == b"new"
    assert store.writes == [b"old", b"new"]


@pytest.mark.anyio
async def test_external_cancellation_drains_active_and_queued_operations():
    store = RecordingStore()
    coordinator = StoreOperationCoordinator(store)
    queued = anyio.Event()

    async def own_coordinator() -> None:
        async with coordinator.lifespan():
            assert not await coordinator.set("key", b"old", timeout_s=0.01)
            assert not await coordinator.set("key", b"new", timeout_s=0.01)
            queued.set()
            await anyio.sleep_forever()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(own_coordinator)
        await queued.wait()
        tasks.cancel_scope.cancel()
        with anyio.CancelScope(shield=True):
            store.release.set()

    assert store.value == b"new"
    assert store.writes == [b"old", b"new"]


@pytest.mark.anyio
async def test_store_operations_require_a_running_coordinator():
    coordinator = StoreOperationCoordinator(RecordingStore())

    with pytest.raises(RuntimeError, match="not running"):
        await coordinator.set("key", b"value", timeout_s=1)


@pytest.mark.anyio
async def test_store_timeout_covers_queue_and_completion():
    sent = anyio.Event()

    class DelayedSend:
        operation = None

        async def send(self, operation):
            await anyio.sleep(0.1)
            self.operation = operation
            sent.set()

    send_stream = DelayedSend()
    coordinator = StoreOperationCoordinator(RecordingStore())
    coordinator.send_stream = send_stream

    async def complete_later() -> None:
        await sent.wait()
        await anyio.sleep(0.1)
        send_stream.operation.completed.set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(complete_later)
        with anyio.fail_after(0.3):
            completed = await coordinator.set("key", b"value", timeout_s=0.15)
        tasks.cancel_scope.cancel()

    assert not completed


@pytest.mark.anyio
async def test_store_operation_coordinator_rejects_duplicate_ownership():
    coordinator = StoreOperationCoordinator(RecordingStore())

    async with coordinator.lifespan():
        with pytest.raises(RuntimeError, match="already running"):
            async with coordinator.lifespan():
                pass


@pytest.mark.anyio
async def test_timed_out_read_finishes_before_a_newer_write():
    started = anyio.Event()
    release = anyio.Event()
    operations = []

    class SlowReadStore:
        value = b"old"

        async def get(self, _key):
            started.set()
            await release.wait()
            operations.append("get")
            return self.value

        async def set(self, _key, value, expires_in=None):
            self.value = value
            operations.append("set")

    store = SlowReadStore()
    coordinator = StoreOperationCoordinator(store)

    async with coordinator.lifespan():
        completed, value = await coordinator.get("key", timeout_s=0.01)
        assert not completed
        assert value is None
        await started.wait()
        writes = []

        async def set_new_value() -> None:
            writes.append(await coordinator.set("key", b"new", timeout_s=1))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(set_new_value)
            await anyio.lowlevel.checkpoint()
            release.set()

    assert writes == [True]
    assert operations == ["get", "set"]
    assert store.value == b"new"
