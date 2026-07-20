"""Tests for result-cache storage and identity."""

from __future__ import annotations

from types import SimpleNamespace

import anyio
import msgspec
import pytest
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

import conda_presto.cache as cache_module
from conda_presto.cache import ResultCache
from conda_presto.resolve import RepodataSnapshot
from conda_presto.solver import PrestoSolveRequest, PrestoSolveResponse


def test_result_cache_uses_lifespan_store_without_registry_fallback():
    store = MemoryStore()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(result_store=store),
            stores=SimpleNamespace(
                get=lambda _name: pytest.fail("store registry fallback was used")
            ),
        )
    )

    assert ResultCache(max_size=1, store_name="results").store_from(request) is store


@pytest.mark.anyio
@pytest.mark.parametrize(
    "backend",
    [pytest.param("memory", id="memory"), pytest.param("file", id="file")],
)
async def test_solver_cache_replaces_one_persistent_entry(
    monkeypatch,
    tmp_path,
    presto_solver_request,
    presto_solver_outcome,
    fresh_repodata_snapshot,
    backend,
):
    store = (
        MemoryStore()
        if backend == "memory"
        else FileStore(tmp_path, create_directories=True)
    )
    cache = ResultCache(max_size=256)
    previous = fresh_repodata_snapshot
    stale = RepodataSnapshot(previous.records, True)
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

    first, first_status = await cache.publish_solver(
        presto_solver_request,
        presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "old"}], neutered=[]),
            previous,
        ),
        store,
    )
    second, second_status = await cache.publish_solver(
        presto_solver_request,
        presto_solver_outcome(
            PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
            stale,
            current,
        ),
        store,
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
async def test_solver_cache_rejects_corrupt_typed_envelope(presto_solver_request):
    store = MemoryStore()
    cache = ResultCache(max_size=256)
    key = ResultCache.solver_key(presto_solver_request.cache_key())
    await store.set(
        key,
        msgspec.msgpack.encode(
            {
                "response": {"records": "not-a-list", "neutered": []},
                "metadata_used": {"records": [], "stale": False},
            }
        ),
    )

    stored = await cache.get_solver_result(presto_solver_request, store)

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
    stale = RepodataSnapshot(previous.records, True)
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

        async def set(self, key, value):
            self.value = value

        async def delete(self, key):
            self.value = None

    store = Store()
    cache = ResultCache(max_size=256)
    monkeypatch.setattr(
        PrestoSolveRequest,
        "repodata_snapshot",
        lambda _: current,
    )
    publication = []

    async def read():
        await cache.get_solver_result(presto_solver_request, store)

    async def publish():
        publication.append(
            await cache.publish_solver(
                presto_solver_request,
                presto_solver_outcome(
                    PrestoSolveResponse(records=[{"name": "new"}], neutered=[]),
                    stale,
                    current,
                ),
                store,
            )
        )

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
def test_result_cache_stores_for_config_registers_backend(
    tmp_path,
    backend,
    expected_type,
):
    stores = ResultCache.stores_for_config(
        backend,
        str(tmp_path) if backend == "file" else None,
        "redis://localhost:6379/0" if backend == "redis" else None,
        "conda-presto-test",
    )

    assert isinstance(stores[cache_module.RESULT_CACHE_STORE_NAME], expected_type)


@pytest.mark.parametrize(
    ("backend", "error"),
    [
        pytest.param("file", "CONDA_PRESTO_RESULT_CACHE_DIR", id="file-dir"),
        pytest.param("sqlite", "Unsupported result cache backend", id="unknown"),
    ],
)
def test_result_cache_stores_for_config_rejects_invalid_config(backend, error):
    with pytest.raises(ValueError, match=error):
        ResultCache.stores_for_config(backend, None, None, "conda-presto")


@pytest.mark.parametrize(
    ("max_bytes", "writes", "expected_retained", "expected_keys", "expected_bytes"),
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
    assert not cache.entries


@pytest.mark.anyio
async def test_result_cache_ignores_failed_corrupt_entry_cleanup():
    class Store:
        async def get(self, key):
            return b"corrupt"

        async def delete(self, key):
            raise RuntimeError("store unavailable")

    cache = ResultCache(max_size=10)

    assert await cache.get_response("key", Store(), location="/r/key") is None


def test_different_output_formats_produce_different_cache_keys():
    default_key = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)
    explicit_key = ResultCache.key_for(
        ["zlib"], ["conda-forge"], ["linux-64"], "explicit"
    )

    assert explicit_key != default_key


@pytest.mark.parametrize("package_name", cache_module.CACHE_DEPENDENCY_PACKAGES)
def test_different_dependency_versions_produce_different_cache_keys(
    monkeypatch,
    package_name,
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
    first = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)
    monkeypatch.setattr(cache_module, "pkg_version", version_two)
    second = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)

    assert second != first


def test_virtual_package_overrides_produce_different_cache_keys(monkeypatch):
    first = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)
    monkeypatch.setitem(cache_module.VIRTUAL_PACKAGES["linux"], "glibc", "9.9")
    second = ResultCache.key_for(["zlib"], ["conda-forge"], ["linux-64"], None)

    assert second != first
