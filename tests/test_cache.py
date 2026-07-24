"""Tests for result-cache storage and identity."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import anyio
import msgspec
import pytest
from conda.models.channel import Channel
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

import conda_presto.cache as cache_module
from conda_presto.cache import ResolveCacheContext, ResultCache, StoredResult
from conda_presto.resolve import RepodataSnapshot
from conda_presto.solver import PrestoSolveRequest, PrestoSolveResponse
from conda_presto.storage import StoreOperationCoordinator


@pytest.fixture()
def resolve_cache_state():
    return {
        "repodata": RepodataSnapshot((), False),
        "solve_context": ResolveCacheContext(
            channel_priority="strict",
            use_only_tar_bz2=False,
            pinned_packages=(),
            allow_cycles=True,
            add_pip_as_python_dependency=True,
            virtual_packages=(("linux-64", ("__linux=1",)),),
        ),
    }


@pytest.mark.anyio
async def test_solver_cache_replaces_one_persistent_entry(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    persistent_store,
):
    store = persistent_store
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)
    previous = fresh_repodata_snapshot
    current = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    snapshots = iter([previous, current])
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: next(snapshots),
    )

    async with store_operations.lifespan():
        first, first_status = await cache.publish_solver(
            presto_solver_request,
            presto_solver_outcome(
                PrestoSolveResponse(records=[{"name": "old"}], neutered=[]),
                previous,
            ),
        )
        second, second_status = await cache.publish_solver(
            presto_solver_request,
            presto_solver_outcome(
                PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
                current,
            ),
        )

    key = ResultCache.solver_key(presto_solver_request.cache_key())
    payload = await store.get(key)
    stored = msgspec.msgpack.decode(payload, type=cache_module.StoredSolverResult)
    assert first_status == second_status == "published"
    assert first.metadata_used == previous
    assert second.metadata_used == current
    assert list(cache.entries) == [key]
    assert stored.response.records == [{"name": "new"}]
    assert stored.metadata_used == current


@pytest.mark.anyio
async def test_solver_cache_keeps_matching_entry_during_publication(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
):
    cache = ResultCache(max_size=256)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    current = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "current"}], neutered=[]),
        metadata_used=fresh_repodata_snapshot,
    )
    cache.remember_memory(key, current)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )

    stored, status = await cache.publish_solver(
        presto_solver_request,
        presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "late"}], neutered=[]),
            fresh_repodata_snapshot,
        ),
    )

    assert status == "already-current"
    assert stored is current
    assert cache.entries[key].response.records == [{"name": "current"}]


@pytest.mark.anyio
async def test_solver_cache_reports_required_persistence_failure(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
):
    class RecoveringStore(MemoryStore):
        attempts = 0

        async def set(self, key, value, expires_in=None):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("store unavailable")
            await super().set(key, value, expires_in)

    repodata = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    current = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "current"}], neutered=[]),
        metadata_used=repodata,
    )
    store = RecoveringStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    cache.remember_memory(key, current)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: repodata,
    )

    outcome = presto_solver_outcome(
        PrestoSolveResponse(records=[{"name": "late"}], neutered=[]),
        repodata,
    )
    async with store_operations.lifespan():
        stored, status = await cache.publish_solver(
            presto_solver_request,
            outcome,
            require_persistent=True,
        )
        retained = key in cache.entries
        recovered = await cache_module.SolverResultService(
            cache=cache,
            require_persistent=True,
        ).inspect(presto_solver_request)

    assert stored is current
    assert status == "persistent-failed"
    assert retained
    assert recovered.cached
    assert cache.entries[key].response.records == [{"name": "current"}]
    assert store.attempts == 2


@pytest.mark.anyio
async def test_solver_cache_does_not_retain_credentialed_requests(
    monkeypatch,
    make_presto_solver_request,
    presto_solver_outcome,
):
    class RecordingStore(MemoryStore):
        def __init__(self):
            super().__init__()
            self.reads = []
            self.writes = []

        async def get(self, key):
            self.reads.append(key)
            return await super().get(key)

        async def set(self, key, value, expires_in=None):
            self.writes.append((key, value))
            await super().set(key, value, expires_in)

    request = make_presto_solver_request(
        channels=[Channel("https://user:secret@repo.example/channel").dump()]
    )
    repodata = RepodataSnapshot((), False)
    outcome = presto_solver_outcome(
        PrestoSolveResponse(
            records=[
                {
                    "name": "zlib",
                    "url": "https://user:secret@repo.example/channel/zlib.conda",
                }
            ],
            neutered=[],
        ),
        repodata,
    )
    monkeypatch.setattr(
        PrestoSolveRequest,
        "cache_key",
        lambda _: pytest.fail("credentialed request reached cache key generation"),
    )
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: pytest.fail("credentialed request reached repodata lookup"),
    )
    store = RecordingStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_solver_result(request) is None
        probe = await cache.inspect_solver_result(request)
        stored, status = await cache.publish_solver(request, outcome)

    assert request.has_detected_credentials()
    assert not probe.cached
    assert probe.current is None
    assert stored is None
    assert status == "not-retained"
    assert cache.entries == {}
    assert store.reads == []
    assert store.writes == []


@pytest.mark.anyio
async def test_solver_cache_does_not_retain_credentialed_results(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)
    outcome = presto_solver_outcome(
        PrestoSolveResponse(
            records=[
                {
                    "name": "zlib",
                    "url": "https://user:secret@cdn.example.test/zlib.conda",
                }
            ],
            neutered=[],
        ),
        fresh_repodata_snapshot,
    )
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: fresh_repodata_snapshot,
    )
    key = ResultCache.solver_key(presto_solver_request.cache_key())

    async with store_operations.lifespan():
        stored, status = await cache.publish_solver(presto_solver_request, outcome)

    assert stored is not None
    assert status == "not-retained"
    assert cache.entries == {}
    assert await store.get(key) is None


@pytest.mark.anyio
async def test_solver_cache_deletes_credentialed_persistent_results(
    monkeypatch,
    presto_solver_request,
    fresh_repodata_snapshot,
):
    stored = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(
            records=[
                {
                    "name": "zlib",
                    "url": "https://cdn.example.test/zlib.conda?signature=secret",
                }
            ],
            neutered=[],
        ),
        metadata_used=fresh_repodata_snapshot,
    )
    store = MemoryStore()
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    await store.set(key, msgspec.msgpack.encode(stored))
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: pytest.fail("credentialed result reached repodata lookup"),
    )

    async with store_operations.lifespan():
        result = await cache.get_solver_result(presto_solver_request)

    assert result is None
    assert await store.get(key) is None


@pytest.mark.anyio
async def test_result_cache_isolates_persistent_read_and_write_errors():
    class FailingStore:
        async def get(self, _key):
            raise RuntimeError("store unavailable")

        async def set(self, _key, _value, expires_in=None):
            raise RuntimeError("store unavailable")

    store_operations = StoreOperationCoordinator(FailingStore())
    cache = ResultCache(max_size=1, store_operations=store_operations)

    async with store_operations.lifespan():
        missing = await cache.get_response("missing", location="/r/missing")
        retention = await cache.store_entry(
            "public",
            cache_module.StoredResult(b"body", "text/plain"),
        )

    assert missing is None
    assert retention.retained
    assert retention.persistent_failed


@pytest.mark.anyio
async def test_result_cache_discards_a_wrong_typed_memory_entry():
    cache = ResultCache(max_size=8)
    stored = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[], neutered=[]),
        metadata_used=RepodataSnapshot((), False),
    )
    cache.remember_memory("key", stored)

    assert await cache.get_response("key", location="/r/key") is None
    assert cache.entries == {}
    assert cache.current_bytes == 0


@pytest.mark.anyio
async def test_result_cache_persistent_read_has_a_caller_deadline(monkeypatch):
    started = anyio.Event()
    release = anyio.Event()

    class SlowStore:
        async def get(self, _key):
            started.set()
            await release.wait()

        async def set(self, _key, _value, expires_in=None):
            return None

    monkeypatch.setattr(cache_module, "RESULT_CACHE_STORE_TIMEOUT_S", 0.01)
    store = SlowStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=8, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_response("key", location="/r/key") is None
        await started.wait()
        release.set()


@pytest.mark.anyio
async def test_solver_cache_advisory_read_keeps_newer_memory_entry(
    monkeypatch,
    presto_solver_request,
):
    previous = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    current = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    old = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "old"}], neutered=[]),
        metadata_used=previous,
    )
    new = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
        metadata_used=current,
    )
    started = anyio.Event()
    release = anyio.Event()

    class Store:
        async def get(self, _key):
            started.set()
            await release.wait()
            return msgspec.msgpack.encode(old)

    store_operations = StoreOperationCoordinator(Store())
    cache = ResultCache(max_size=256, store_operations=store_operations)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: current,
    )
    states = []

    async def inspect():
        states.append(await cache.inspect_solver_result(presto_solver_request))

    async with store_operations.lifespan():
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(inspect)
            await started.wait()
            cache.remember_memory(key, new)
            release.set()

    assert states[0].cached
    assert cache.entries[key] is new


@pytest.mark.anyio
async def test_solver_cache_persists_current_entry_not_retained_in_memory(
    monkeypatch,
    presto_solver_request,
):
    class CountingStore(MemoryStore):
        writes = 0

        async def set(self, key, value, expires_in=None):
            self.writes += 1
            await super().set(key, value, expires_in)

    repodata = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    stored = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "python"}], neutered=[]),
        metadata_used=repodata,
    )
    store = CountingStore()
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    await store.set(key, msgspec.msgpack.encode(stored))
    store.writes = 0
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(
        max_size=8,
        max_bytes=1,
        store_operations=store_operations,
    )
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _request: repodata,
    )

    async with store_operations.lifespan():
        probe = await cache_module.SolverResultService(
            cache,
            require_persistent=True,
        ).inspect(presto_solver_request)

    assert probe.cached
    assert not probe.persistence_failed
    assert store.writes == 1
    assert cache.entries == {}


@pytest.mark.anyio
async def test_solver_cache_rejects_corrupt_typed_envelope(
    presto_solver_request,
):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    payload = msgspec.msgpack.encode(
        {
            "response": {"records": "not-a-list", "neutered": []},
            "metadata_used": {"records": [], "stale": False},
        }
    )
    await store.set(key, payload)

    async with store_operations.lifespan():
        stored = await cache.get_solver_result(presto_solver_request)

    assert stored is None
    assert await store.get(key) is None


@pytest.mark.anyio
async def test_solver_cache_serializes_persistent_read_and_publication(
    monkeypatch,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
):
    previous = fresh_repodata_snapshot
    current = RepodataSnapshot(
        (("https://conda.example/linux-64", "repodata.json", 20, 2),),
        False,
    )
    old = cache_module.StoredSolverResult(
        response=PrestoSolveResponse(records=[{"name": "old"}], neutered=[]),
        metadata_used=previous,
    )
    started = anyio.Event()
    release = anyio.Event()

    class Store:
        value = msgspec.msgpack.encode(old)

        async def get(self, key):
            started.set()
            await release.wait()
            return self.value

        async def set(self, key, value, expires_in=None):
            self.value = value

        async def delete(self, key):
            self.value = None

    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=256, store_operations=store_operations)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: current,
    )
    publication = []

    async def read():
        await cache.get_solver_result(presto_solver_request)

    async def publish():
        publication.append(
            await cache.publish_solver(
                presto_solver_request,
                presto_solver_outcome(
                    PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
                    current,
                ),
            )
        )

    async with store_operations.lifespan():
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(read)
            await started.wait()
            tasks.start_soon(publish)
            await anyio.sleep(0)
            release.set()

    key = ResultCache.solver_key(presto_solver_request.cache_key())
    assert publication[0][1] == "published"
    assert cache.entries[key].response.records == [{"name": "new"}]
    stored = msgspec.msgpack.decode(
        store.value,
        type=cache_module.StoredSolverResult,
    )
    assert stored.response.records == [{"name": "new"}]


@pytest.mark.parametrize(
    ("backend", "expected_type"),
    [
        pytest.param("file", FileStore, id="file"),
        pytest.param("redis", RedisStore, id="redis"),
    ],
)
def test_result_cache_store_for_config_returns_store(
    tmp_path,
    backend,
    expected_type,
):
    store = ResultCache.store_for_config(
        backend,
        str(tmp_path) if backend == "file" else None,
        "redis://localhost:6379/0" if backend == "redis" else None,
        "conda-presto-test",
    )

    assert isinstance(store, expected_type)


@pytest.mark.parametrize(
    "backend, error",
    [
        pytest.param("file", "CONDA_PRESTO_RESULT_CACHE_DIR", id="file-dir"),
        pytest.param("sqlite", "Unsupported result cache backend", id="unknown"),
    ],
)
def test_result_cache_store_for_config_rejects_invalid_config(backend, error):
    with pytest.raises(ValueError, match=error):
        ResultCache.store_for_config(backend, None, None, "conda-presto")


@pytest.mark.parametrize(
    "max_bytes, writes, expected_retained, expected_keys, expected_bytes",
    [
        pytest.param(
            30,
            (("first", 20), ("second", 20)),
            (True, True),
            ["second"],
            30,
            id="evict-oldest-by-bytes",
        ),
        pytest.param(
            100,
            (("same", 10), ("same", 20)),
            (True, True),
            ["same"],
            30,
            id="replace-existing-accounting",
        ),
        pytest.param(
            10,
            (("oversized", 20),),
            (False,),
            [],
            0,
            id="skip-oversized",
        ),
        pytest.param(
            20,
            (("same", 5), ("same", 20)),
            (True, False),
            [],
            0,
            id="drop-existing-oversized-replacement",
        ),
    ],
)
def test_result_cache_memory_limit(
    max_bytes,
    writes,
    expected_retained,
    expected_keys,
    expected_bytes,
):
    cache = ResultCache(max_size=10, max_bytes=max_bytes)

    retained = tuple(
        cache.remember_memory(
            key,
            cache_module.StoredResult(b"x" * body_size, "text/plain"),
        )
        for key, body_size in writes
    )

    assert retained == expected_retained
    assert list(cache.entries) == expected_keys
    assert cache.current_bytes == expected_bytes


@pytest.mark.anyio
async def test_result_cache_remember_omits_permalink_when_memory_rejects_result():
    cache = ResultCache(max_size=10, max_bytes=10)

    response = await cache.remember(
        "oversized",
        b"x" * 20,
        "text/plain",
        location="/r/oversized",
    )

    assert "Location" not in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert not cache.entries


@pytest.mark.anyio
async def test_result_cache_uses_no_store_until_immutable_permalink_lookup():
    cache = ResultCache(max_size=10)
    await cache.remember(
        "key",
        b"body",
        "text/plain",
        location="/r/key",
    )

    mutable = await cache.get_response("key", location="/r/key")
    immutable = await cache.get_response(
        "key",
        location="/r/key",
        immutable=True,
    )

    assert mutable.headers["Cache-Control"] == "no-store"
    assert immutable.headers["Cache-Control"] == ("public, max-age=86400, immutable")


@pytest.mark.anyio
async def test_result_cache_can_return_a_credentialed_result_without_retaining_it():
    cache = ResultCache(max_size=10)

    response = await cache.remember(
        "key",
        b"body",
        "text/plain",
        location="/r/key",
        retain=False,
    )

    assert response.headers["Cache-Control"] == "no-store"
    assert "Location" not in response.headers
    assert cache.entries == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "media_type"),
    [
        pytest.param(
            b'[{"url":"https://public.example.test/python.conda"},'
            b'{"url":"https://user:secret@cdn.example.test/zlib.conda"}]',
            "application/json",
            id="native-json",
        ),
        pytest.param(
            b"@EXPLICIT\nhttps://cdn.example.test/zlib.conda?signature=secret\n",
            "text/plain",
            id="exporter",
        ),
    ],
)
async def test_result_cache_does_not_retain_credentialed_output(body, media_type):
    store = MemoryStore()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        response = await cache.remember(
            "key",
            body,
            media_type,
            location="/r/key",
        )

    assert response.content == body
    assert response.headers["Cache-Control"] == "no-store"
    assert "Location" not in response.headers
    assert cache.entries == {}
    assert await store.get("key") is None


@pytest.mark.anyio
async def test_result_cache_deletes_credentialed_persistent_output():
    store = MemoryStore()
    await store.set(
        "key",
        msgspec.msgpack.encode(
            StoredResult(
                body=b"https://user:secret@cdn.example.test/zlib.conda",
                media_type="text/plain",
            )
        ),
    )
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        response = await cache.get_response("key", location="/r/key")

    assert response is None
    assert await store.get("key") is None


@pytest.mark.anyio
async def test_result_cache_discards_credentialed_memory_output():
    cache = ResultCache(max_size=10)
    stored = StoredResult(
        body=b"https://user:secret@cdn.example.test/zlib.conda",
        media_type="text/plain",
    )
    cache.entries["key"] = stored
    cache.current_bytes = stored.memory_size

    response = await cache.get_response("key", location="/r/key")

    assert response is None
    assert cache.entries == {}
    assert cache.current_bytes == 0


def test_result_cache_removes_previous_output_when_replacement_has_credentials():
    cache = ResultCache(max_size=10)
    cache.remember_memory("key", StoredResult(b"public", "text/plain"))

    retained = cache.remember_memory(
        "key",
        StoredResult(
            b"https://user:secret@cdn.example.test/zlib.conda",
            "text/plain",
        ),
    )

    assert not retained
    assert cache.entries == {}
    assert cache.current_bytes == 0


@pytest.mark.anyio
async def test_persistent_result_cache_entries_expire():
    class Store:
        expires_in = None

        async def set(self, _key, _value, *, expires_in=None):
            self.expires_in = expires_in

    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        await cache.store_entry(
            "key",
            cache_module.StoredResult(b"body", "text/plain"),
        )

    assert store.expires_in == cache_module.RESULT_CACHE_STORE_MAX_AGE_S


@pytest.mark.anyio
async def test_result_cache_ignores_corrupt_persistent_entry():
    class Store:
        value = b"corrupt"

        async def get(self, key):
            return self.value

        async def delete(self, key):
            self.value = None

    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_response("key", location="/r/key") is None

    assert store.value is None


@pytest.mark.anyio
async def test_result_cache_deletes_oversized_persistent_entry(monkeypatch):
    class Store:
        value = b"large"

        async def get(self, key):
            return self.value

        async def delete(self, key):
            self.value = None

    monkeypatch.setattr(cache_module, "RESULT_CACHE_MAX_STORED_BYTES", 4)
    store = Store()
    store_operations = StoreOperationCoordinator(store)
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        assert await cache.get_response("key", location="/r/key") is None

    assert store.value is None


@pytest.mark.anyio
async def test_result_cache_rejects_oversized_persistent_write(monkeypatch):
    monkeypatch.setattr(cache_module, "RESULT_CACHE_MAX_STORED_BYTES", 1)
    store_operations = StoreOperationCoordinator(MemoryStore())
    cache = ResultCache(max_size=10, store_operations=store_operations)

    async with store_operations.lifespan():
        retention = await cache.store_entry(
            "key",
            StoredResult(body=b"large", media_type="application/json"),
        )

    assert retention.retained
    assert retention.persistent_failed


def test_different_output_formats_produce_different_cache_keys(resolve_cache_state):
    default_key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )
    explicit_key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        "explicit",
        exporter_identity=(
            "explicit",
            "conda.plugins.environment_exporters.explicit:export_explicit",
            (("conda", "test"),),
        ),
        **resolve_cache_state,
    )

    assert explicit_key != default_key


def test_result_cache_key_covers_protocol_version(monkeypatch, resolve_cache_state):
    first = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )
    monkeypatch.setattr(cache_module, "CACHE_ENVELOPE_VERSION", 7)

    assert (
        ResultCache.key_for(
            ["zlib"],
            ["conda-forge"],
            ["linux-64"],
            None,
            **resolve_cache_state,
        )
        != first
    )


def test_result_cache_key_captures_default_state(monkeypatch, resolve_cache_state):
    calls = []
    monkeypatch.setattr(
        RepodataSnapshot,
        "capture",
        lambda channels, platforms: (
            calls.append((channels, platforms)) or resolve_cache_state["repodata"]
        ),
    )
    monkeypatch.setattr(
        ResolveCacheContext,
        "capture",
        lambda platforms: (
            calls.append(platforms) or resolve_cache_state["solve_context"]
        ),
    )

    key = ResultCache.key_for(["zlib"], ["conda-forge"], None, None)

    assert len(key) == 64
    assert calls == [
        (["conda-forge"], [cache_module.NATIVE_SUBDIR]),
        [cache_module.NATIVE_SUBDIR],
    ]


def test_result_cache_key_tolerates_missing_dependency_version(
    monkeypatch,
    resolve_cache_state,
):
    def missing_version(_):
        raise ModuleNotFoundError

    monkeypatch.setattr(cache_module, "pkg_version", missing_version)

    key = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )

    assert len(key) == 64


def test_empty_output_format_does_not_collide_with_native(resolve_cache_state):
    native = ResultCache.key_for(
        ["zlib"], ["conda-forge"], ["linux-64"], None, **resolve_cache_state
    )
    empty = ResultCache.key_for(
        ["zlib"], ["conda-forge"], ["linux-64"], "", **resolve_cache_state
    )

    assert empty != native


def test_exporter_provider_version_changes_cache_key(resolve_cache_state):
    request = (["zlib"], ["conda-forge"], ["linux-64"], "third-party")
    first = ResultCache.key_for(
        *request,
        exporter_identity=(
            "third-party",
            "third_party.exporter:export",
            (("third-party", "1"),),
        ),
        **resolve_cache_state,
    )
    second = ResultCache.key_for(
        *request,
        exporter_identity=(
            "third-party",
            "third_party.exporter:export",
            (("third-party", "2"),),
        ),
        **resolve_cache_state,
    )

    assert second != first


@pytest.mark.parametrize("package_name", cache_module.CACHE_DEPENDENCY_PACKAGES)
def test_different_dependency_versions_produce_different_cache_keys(
    monkeypatch,
    package_name,
    resolve_cache_state,
):
    def version_one(package):
        if package == package_name:
            return "one"
        return "test"

    def version_two(package):
        if package == package_name:
            return "two"
        return "test"

    monkeypatch.setattr(cache_module, "pkg_version", version_one)
    first = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )
    monkeypatch.setattr(cache_module, "pkg_version", version_two)
    second = ResultCache.key_for(
        ["zlib"],
        ["conda-forge"],
        ["linux-64"],
        None,
        **resolve_cache_state,
    )

    assert second != first


def test_virtual_packages_produce_different_cache_keys(resolve_cache_state):
    first_context = resolve_cache_state["solve_context"]
    second_context = replace(
        first_context,
        virtual_packages=(("linux-64", ("__glibc=9.9", "__linux=1")),),
    )
    request = (["zlib"], ["conda-forge"], ["linux-64"], None)
    repodata = resolve_cache_state["repodata"]

    first = ResultCache.key_for(
        *request,
        repodata=repodata,
        solve_context=first_context,
    )
    second = ResultCache.key_for(
        *request,
        repodata=repodata,
        solve_context=second_context,
    )

    assert second != first


def test_resolve_cache_context_captures_cuda_override(monkeypatch):
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "12.0")
    first = ResolveCacheContext.capture(["linux-64"])
    monkeypatch.setenv("CONDA_OVERRIDE_CUDA", "13.0")
    second = ResolveCacheContext.capture(["linux-64"])

    assert first.virtual_packages != second.virtual_packages


@pytest.mark.parametrize(
    ("first_context", "second_context"),
    [
        pytest.param(
            {"channel_priority": "strict"},
            {"channel_priority": "disabled"},
            id="channel-priority",
        ),
        pytest.param(
            {"use_only_tar_bz2": False},
            {"use_only_tar_bz2": True},
            id="package-format",
        ),
        pytest.param(
            {"pinned_packages": ("python<3.13",)},
            {"pinned_packages": ("python<3.14",)},
            id="pinned-packages",
        ),
        pytest.param(
            {"allow_cycles": True},
            {"allow_cycles": False},
            id="allow-cycles",
        ),
        pytest.param(
            {"add_pip_as_python_dependency": True},
            {"add_pip_as_python_dependency": False},
            id="add-pip",
        ),
    ],
)
def test_solve_context_produces_different_cache_keys(
    first_context,
    second_context,
    resolve_cache_state,
):
    request = (["zlib"], ["conda-forge"], ["linux-64"], None)
    base = resolve_cache_state["solve_context"]

    first = ResultCache.key_for(
        *request,
        repodata=resolve_cache_state["repodata"],
        solve_context=replace(base, **first_context),
    )
    second = ResultCache.key_for(
        *request,
        repodata=resolve_cache_state["repodata"],
        solve_context=replace(base, **second_context),
    )

    assert first != second


@pytest.mark.parametrize(
    "channel",
    [
        pytest.param("https://user:secret@repo.example/channel", id="basic-auth"),
        pytest.param("https://repo.example/t/secret/channel", id="token"),
        pytest.param("https://repo.example/%2574%252Fsecret/channel", id="encoded"),
        pytest.param("https://repo.example/channel?signature=secret", id="query"),
    ],
)
def test_result_cache_detects_credentialed_channels(channel):
    assert ResultCache.channels_have_credentials([channel])


def test_result_cache_accepts_public_channels():
    assert not ResultCache.channels_have_credentials(
        ["conda-forge", "https://repo.example/channel"]
    )


def test_result_cache_detects_credentials_in_custom_multichannel(monkeypatch):
    monkeypatch.setattr(
        cache_module,
        "context",
        SimpleNamespace(
            custom_multichannels={
                "private": (
                    Channel("https://user:secret@example.test/t/token/channel"),
                )
            }
        ),
    )

    assert ResultCache.channels_have_credentials(["private"])
    assert ResultCache.specs_have_credentials(["private::zlib"])


@pytest.mark.parametrize("spec", ["zlib%ZZ", "["])
def test_result_cache_does_not_retain_unparseable_specs(spec):
    assert ResultCache.specs_have_credentials([spec])


def test_result_cache_does_not_retain_unparseable_channels(monkeypatch):
    def invalid_channel(_):
        raise ValueError("invalid channel")

    monkeypatch.setattr(cache_module, "Channel", invalid_channel)

    assert ResultCache.channels_have_credentials(["channel"])
