"""Tests for replayable solver hot-set state."""

from __future__ import annotations

import anyio
import msgspec
import pytest
from conda.models.channel import Channel
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

import conda_presto.hotset as hotset_module
import conda_presto.solver as solver_module
from conda_presto.hotset import (
    SOLVER_HOTSET_MAX_AGE_S,
    SOLVER_HOTSET_SCORE_HALF_LIFE_S,
    SOLVER_HOTSET_STORE_KEY,
    SolverHotSet,
    SolverHotSetCatalog,
)
from conda_presto.resolve import RepodataSnapshot
from conda_presto.solver import PrestoSolveRequest


@pytest.fixture()
def solver_request():
    return PrestoSolveRequest(
        channels=[Channel("conda-forge").dump()],
        subdirs=["linux-64", "noarch"],
        specs_to_add=["zlib"],
        specs_to_remove=[],
        installed=[
            {
                "name": "python",
                "version": "3.13.5",
                "build": "h123_0",
                "build_number": 0,
                "subdir": "linux-64",
                "url": (
                    "https://conda.anaconda.org/conda-forge/linux-64/"
                    "python-3.13.5-h123_0.conda"
                ),
            }
        ],
        history=["python=3.13", "zlib"],
        pinned=["python<3.14"],
        virtual=[{"name": "__linux", "version": "6.12", "build": "0"}],
        aggressive_updates=["openssl"],
        always_update=["ca-certificates"],
        update_modifier="UPDATE_SPECS",
        deps_modifier="NOT_SET",
        ignore_pinned=False,
        force_remove=False,
        prune=False,
        command="install",
        repodata_fn="repodata.json",
        offline=False,
        channel_priority="strict",
        use_only_tar_bz2=False,
        add_pip_as_python_dependency=True,
        allow_cycles=True,
        restore_free_channel=False,
        repodata_use_shards=True,
        use_index_cache=False,
    )


@pytest.fixture(params=["memory", "file", "redis"])
def persistent_store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "file":
        return FileStore(tmp_path / "hotset", create_directories=True)

    class RedisClient:
        def __init__(self):
            self.data = {}

        def register_script(self, _script):
            return None

        async def get(self, key):
            return self.data.get(key)

        async def set(self, key, value, *, ex=None):
            self.data[key] = value

        async def delete(self, key):
            self.data.pop(key, None)

    return RedisStore(RedisClient(), namespace="test")


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            {"channels": [Channel("bioconda").dump()]},
            id="channels",
        ),
        pytest.param({"subdirs": ["osx-arm64", "noarch"]}, id="subdirs"),
        pytest.param({"specs_to_add": ["python"]}, id="specs-to-add"),
        pytest.param({"specs_to_remove": ["zlib"]}, id="specs-to-remove"),
        pytest.param(
            {"installed": [{"name": "python", "version": "3.12"}]},
            id="installed",
        ),
        pytest.param({"history": ["python=3.12"]}, id="history"),
        pytest.param({"pinned": ["python<3.13"]}, id="pinned"),
        pytest.param(
            {"virtual": [{"name": "__glibc", "version": "2.40"}]},
            id="virtual",
        ),
        pytest.param(
            {"aggressive_updates": ["python"]},
            id="aggressive-updates",
        ),
        pytest.param({"always_update": ["python"]}, id="always-update"),
        pytest.param({"update_modifier": "UPDATE_ALL"}, id="update-modifier"),
        pytest.param({"deps_modifier": "NO_DEPS"}, id="deps-modifier"),
        pytest.param({"ignore_pinned": True}, id="ignore-pinned"),
        pytest.param({"force_remove": True}, id="force-remove"),
        pytest.param({"prune": True}, id="prune"),
        pytest.param({"command": "create"}, id="command"),
        pytest.param({"repodata_fn": "custom.json"}, id="repodata"),
        pytest.param({"offline": True}, id="offline"),
        pytest.param(
            {"channel_priority": "flexible"},
            id="channel-priority",
        ),
        pytest.param({"use_only_tar_bz2": True}, id="package-format"),
        pytest.param(
            {"add_pip_as_python_dependency": False},
            id="pip-dependency",
        ),
        pytest.param({"allow_cycles": False}, id="allow-cycles"),
        pytest.param(
            {"restore_free_channel": True},
            id="restore-free-channel",
        ),
        pytest.param({"repodata_use_shards": False}, id="repodata-shards"),
        pytest.param({"use_index_cache": True}, id="index-cache"),
        pytest.param(
            {"channels": [{**Channel("conda-forge").dump(), "auth": "user:password"}]},
            id="channel-auth",
        ),
        pytest.param(
            {"channels": [{**Channel("conda-forge").dump(), "token": "secret"}]},
            id="channel-token",
        ),
    ],
)
def test_workload_key_covers_replayable_state(solver_request, change):
    changed = msgspec.structs.replace(solver_request, **change)

    assert changed.workload_key() != solver_request.workload_key()


def test_workload_key_preserves_effective_channel_order(solver_request):
    first = msgspec.structs.replace(
        solver_request,
        channels=[Channel("conda-forge").dump(), Channel("bioconda").dump()],
    )
    second = msgspec.structs.replace(
        solver_request,
        channels=[Channel("bioconda").dump(), Channel("conda-forge").dump()],
    )

    assert first.workload_key() != second.workload_key()


def test_workload_key_covers_protocol_version(monkeypatch, solver_request):
    first = solver_request.workload_key()
    monkeypatch.setattr(solver_module, "SOLVER_WORKLOAD_ENVELOPE_VERSION", 2)

    assert solver_request.workload_key() != first


def test_workload_key_uses_effective_repodata_filename(monkeypatch, solver_request):
    current = msgspec.structs.replace(
        solver_request,
        repodata_fn="current_repodata.json",
    )
    monkeypatch.setattr(
        solver_module,
        "maybe_ignore_current_repodata",
        lambda _value: "repodata.json",
    )

    assert current.workload_key() == solver_request.workload_key()


def test_workload_key_excludes_dependency_versions(monkeypatch, solver_request):
    monkeypatch.setattr(solver_module, "pkg_version", lambda _name: "one")
    first_workload = solver_request.workload_key()
    first_cache = solver_request.cache_key()
    monkeypatch.setattr(solver_module, "pkg_version", lambda _name: "two")

    assert solver_request.workload_key() == first_workload
    assert solver_request.cache_key() != first_cache


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(
            {"channels": [{**Channel("conda-forge").dump(), "auth": "user:password"}]},
            id="channel-auth",
        ),
        pytest.param(
            {"channels": [{**Channel("conda-forge").dump(), "token": "secret"}]},
            id="channel-token",
        ),
        pytest.param(
            {
                "channels": [
                    {
                        **Channel("conda-forge").dump(),
                        "nested": {"password": "secret"},
                    }
                ]
            },
            id="nested-password",
        ),
        pytest.param(
            {
                "installed": [
                    {
                        "name": "python",
                        "url": "https://user:secret@repo.example/linux-64/python.conda",
                    }
                ]
            },
            id="basic-auth-url",
        ),
        pytest.param(
            {
                "installed": [
                    {
                        "name": "python",
                        "url": ("https://repo.example/t/secret/linux-64/python.conda"),
                    }
                ]
            },
            id="token-url",
        ),
        pytest.param(
            {
                "installed": [
                    {
                        "name": "python",
                        "url": (
                            "https://repo.example/linux-64/python.conda"
                            "?X-Amz-Signature=secret"
                        ),
                    }
                ]
            },
            id="signed-url-query",
        ),
        pytest.param(
            {
                "installed": [
                    {
                        "name": "python",
                        "url": ("https://repo.example/linux-64/python.conda#secret"),
                    }
                ]
            },
            id="url-fragment",
        ),
    ],
)
def test_request_detects_credentials(solver_request, change):
    request = msgspec.structs.replace(solver_request, **change)

    assert request.contains_credentials()


def test_public_request_is_credential_free(solver_request):
    assert not solver_request.contains_credentials()


def test_hot_set_requires_two_observations(solver_request):
    hot_set = SolverHotSet(max_size=32)

    assert hot_set.observe(solver_request, now=10)
    assert hot_set.candidates(limit=8, now=10) == ()
    assert hot_set.observe(solver_request, now=20)

    candidates = hot_set.candidates(limit=8, now=20)
    assert len(candidates) == 1
    assert candidates[0].observations == 2
    assert candidates[0].last_seen == 20


def test_hot_set_decays_scores_with_24_hour_half_life(solver_request):
    hot_set = SolverHotSet(max_size=32)

    hot_set.observe(solver_request, now=0)
    hot_set.observe(solver_request, now=SOLVER_HOTSET_SCORE_HALF_LIFE_S)

    entry = next(iter(hot_set.entries.values()))
    assert entry.score == pytest.approx(1.5)
    assert entry.score_at(2 * SOLVER_HOTSET_SCORE_HALF_LIFE_S) == pytest.approx(0.75)


def test_hot_set_expires_entries_after_seven_days(solver_request):
    hot_set = SolverHotSet(max_size=32)
    hot_set.observe(solver_request, now=0)
    hot_set.observe(solver_request, now=1)

    candidates = hot_set.candidates(
        limit=8,
        now=SOLVER_HOTSET_MAX_AGE_S + 2,
    )

    assert candidates == ()
    assert hot_set.entries == {}
    assert hot_set.current_bytes == 0


def test_hot_set_ranks_by_score_then_recency(solver_request):
    hot_set = SolverHotSet(max_size=32)
    older = msgspec.structs.replace(solver_request, specs_to_add=["older"])
    newer = msgspec.structs.replace(solver_request, specs_to_add=["newer"])
    hottest = msgspec.structs.replace(solver_request, specs_to_add=["hottest"])
    for request in (older, newer, hottest):
        hot_set.observe(request, now=10)
        hot_set.observe(request, now=10)
    hot_set.observe(hottest, now=10)
    hot_set.observe(hottest, now=10)
    hot_set.observe(newer, now=20)

    candidates = hot_set.candidates(limit=3, now=20)

    assert [candidate.fingerprint for candidate in candidates] == [
        hottest.workload_key(),
        newer.workload_key(),
        older.workload_key(),
    ]


def test_hot_set_uses_fingerprint_as_final_ranking_tie_breaker(solver_request):
    hot_set = SolverHotSet(max_size=32)
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("alpha", "bravo")
    ]
    for request in requests:
        hot_set.observe(request, now=10)
        hot_set.observe(request, now=10)

    candidates = hot_set.candidates(limit=2, now=10)

    assert [candidate.fingerprint for candidate in candidates] == sorted(
        request.workload_key() for request in requests
    )


def test_hot_set_evicts_lowest_ranked_entry_at_count_limit(solver_request):
    hot_set = SolverHotSet(max_size=2)
    hottest = msgspec.structs.replace(solver_request, specs_to_add=["hottest"])
    oldest = msgspec.structs.replace(solver_request, specs_to_add=["oldest"])
    newest = msgspec.structs.replace(solver_request, specs_to_add=["newest"])
    hot_set.observe(hottest, now=0)
    hot_set.observe(hottest, now=0)
    hot_set.observe(oldest, now=1)
    hot_set.observe(newest, now=2)

    assert set(hot_set.entries) == {
        hottest.workload_key(),
        newest.workload_key(),
    }


def test_hot_set_evicts_lowest_ranked_entry_at_byte_limit(solver_request):
    first = msgspec.structs.replace(solver_request, specs_to_add=["aa"])
    second = msgspec.structs.replace(solver_request, specs_to_add=["bb"])
    request_size = len(msgspec.msgpack.encode(first))
    assert len(msgspec.msgpack.encode(second)) == request_size
    hot_set = SolverHotSet(max_size=32, max_bytes=request_size)

    hot_set.observe(first, now=1)
    hot_set.observe(second, now=2)

    assert list(hot_set.entries) == [second.workload_key()]
    assert hot_set.current_bytes == request_size


def test_hot_set_skips_request_larger_than_byte_limit(solver_request):
    request_size = len(msgspec.msgpack.encode(solver_request))
    hot_set = SolverHotSet(max_size=32, max_bytes=request_size - 1)

    assert not hot_set.observe(solver_request, now=1)
    assert hot_set.entries == {}
    assert hot_set.generation == 0


def test_zero_size_disables_observation(solver_request):
    hot_set = SolverHotSet(max_size=0)

    assert not hot_set.observe(solver_request, now=1)
    assert hot_set.entries == {}


def test_warm_and_failure_markers_do_not_increase_demand(
    solver_request,
):
    hot_set = SolverHotSet(max_size=32)
    hot_set.observe(solver_request, now=1)
    hot_set.observe(solver_request, now=2)
    fingerprint = solver_request.workload_key()
    entry = hot_set.entries[fingerprint]
    demand = (entry.score, entry.observations, entry.last_seen)

    hot_set.mark_transient_failure(fingerprint, retry_at=100)
    assert hot_set.candidates(limit=1, now=99) == ()
    assert entry.retry_at == 100
    assert entry.consecutive_transient_failures == 1
    assert entry.failed_repodata_records is None

    repodata = RepodataSnapshot(
        (("https://repo.example/linux-64", "repodata.json", 10, 1),),
        False,
    )
    hot_set.mark_deterministic_failure(fingerprint, repodata)
    assert entry.failed_repodata_records == repodata.records
    assert entry.retry_at == 0
    assert entry.consecutive_transient_failures == 0

    hot_set.mark_warm(fingerprint)
    assert entry.failed_repodata_records is None
    assert entry.retry_at == 0
    assert entry.consecutive_transient_failures == 0
    assert (entry.score, entry.observations, entry.last_seen) == demand


def test_foreground_success_clears_failure_markers(solver_request):
    hot_set = SolverHotSet(max_size=32)
    hot_set.observe(solver_request, now=1)
    fingerprint = solver_request.workload_key()
    hot_set.mark_transient_failure(fingerprint, retry_at=100)

    hot_set.observe(solver_request, now=2)

    entry = hot_set.entries[fingerprint]
    assert entry.observations == 2
    assert entry.failed_repodata_records is None
    assert entry.retry_at == 0
    assert entry.consecutive_transient_failures == 0


@pytest.mark.anyio
async def test_persistent_hot_set_round_trip(
    persistent_store,
    solver_request,
):
    source = SolverHotSet(max_size=32, persist=True)
    source.observe(solver_request, now=10)
    source.observe(solver_request, now=20)

    await source.checkpoint(persistent_store, now=20)
    restored = SolverHotSet(max_size=32, persist=True)
    await restored.load(persistent_store, now=30)

    fingerprint = solver_request.workload_key()
    assert list(restored.entries) == [fingerprint]
    assert restored.entries[fingerprint].request == solver_request
    assert restored.entries[fingerprint].observations == 2
    assert restored.current_bytes == len(msgspec.msgpack.encode(solver_request))
    assert len(restored.candidates(limit=1, now=30)) == 1


@pytest.mark.anyio
async def test_persistence_is_opt_in(solver_request):
    store = MemoryStore()
    source = SolverHotSet(max_size=32)
    source.observe(solver_request, now=1)
    await source.checkpoint(store, now=1)
    assert await store.get(SOLVER_HOTSET_STORE_KEY) is None

    payload = msgspec.msgpack.encode(
        SolverHotSetCatalog(entries=list(source.entries.values()))
    )
    await store.set(SOLVER_HOTSET_STORE_KEY, payload)
    restored = SolverHotSet(max_size=32)
    await restored.load(store, now=1)
    assert restored.entries == {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"not-msgpack", id="corrupt"),
        pytest.param(
            msgspec.msgpack.encode({"version": 2, "entries": []}),
            id="incompatible-version",
        ),
    ],
)
async def test_load_discards_corrupt_or_incompatible_catalog(payload):
    store = MemoryStore()
    await store.set(SOLVER_HOTSET_STORE_KEY, payload)
    hot_set = SolverHotSet(max_size=32, persist=True)

    await hot_set.load(store, now=1)

    assert hot_set.entries == {}
    assert await store.get(SOLVER_HOTSET_STORE_KEY) is None


@pytest.mark.anyio
async def test_load_filters_expired_entries(solver_request):
    store = MemoryStore()
    source = SolverHotSet(max_size=32, persist=True)
    source.observe(solver_request, now=0)
    await source.checkpoint(store, now=0)
    restored = SolverHotSet(max_size=32, persist=True)

    await restored.load(store, now=SOLVER_HOTSET_MAX_AGE_S + 1)

    assert restored.entries == {}
    assert restored.current_bytes == 0
    assert restored.generation == 1


@pytest.mark.anyio
async def test_load_enforces_local_count_limit(solver_request):
    store = MemoryStore()
    source = SolverHotSet(max_size=32, persist=True)
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("cold", "warm", "hot")
    ]
    for observations, request in enumerate(requests, start=1):
        for _ in range(observations):
            source.observe(request, now=10)
    await source.checkpoint(store, now=10)
    restored = SolverHotSet(max_size=1, persist=True)

    await restored.load(store, now=10)

    assert list(restored.entries) == [requests[-1].workload_key()]


@pytest.mark.anyio
async def test_store_failures_are_best_effort(solver_request):
    class FailingStore:
        async def get(self, _key):
            raise OSError("read failed")

        async def set(self, _key, _value, *, expires_in=None):
            raise OSError("write failed")

        async def delete(self, _key):
            raise OSError("delete failed")

    hot_set = SolverHotSet(max_size=32, persist=True)
    hot_set.observe(solver_request, now=1)

    await hot_set.load(FailingStore(), now=1)
    await hot_set.checkpoint(FailingStore(), now=1)

    assert solver_request.workload_key() in hot_set.entries
    assert hot_set.persisted_generation == 0


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["load", "checkpoint"])
async def test_store_timeouts_are_best_effort(monkeypatch, solver_request, operation):
    class SlowStore:
        async def get(self, _key):
            await anyio.sleep_forever()

        async def set(self, _key, _value, *, expires_in=None):
            await anyio.sleep_forever()

    monkeypatch.setattr(hotset_module, "SOLVER_HOTSET_STORE_TIMEOUT_S", 0)
    hot_set = SolverHotSet(max_size=32, persist=True)
    hot_set.observe(solver_request, now=1)

    await getattr(hot_set, operation)(SlowStore(), now=1)

    assert solver_request.workload_key() in hot_set.entries
    assert hot_set.persisted_generation == 0


@pytest.mark.anyio
async def test_credential_bearing_entries_remain_memory_only(solver_request):
    request = msgspec.structs.replace(
        solver_request,
        channels=[{**Channel("conda-forge").dump(), "token": "secret"}],
    )
    hot_set = SolverHotSet(max_size=32, persist=True)
    hot_set.observe(request, now=1)
    hot_set.observe(request, now=2)
    store = MemoryStore()

    await hot_set.checkpoint(store, now=2)

    fingerprint = request.workload_key()
    assert fingerprint in hot_set.entries
    assert len(hot_set.candidates(limit=1, now=2)) == 1
    payload = await store.get(SOLVER_HOTSET_STORE_KEY)
    catalog = msgspec.msgpack.decode(payload, type=SolverHotSetCatalog)
    assert catalog.entries == []


@pytest.mark.anyio
async def test_load_rejects_persisted_credential_bearing_entry(solver_request):
    request = msgspec.structs.replace(
        solver_request,
        installed=[
            {
                "name": "python",
                "url": "https://repo.example/t/secret/linux-64/python.conda",
            }
        ],
    )
    source = SolverHotSet(max_size=32)
    source.observe(request, now=1)
    payload = msgspec.msgpack.encode(
        SolverHotSetCatalog(entries=list(source.entries.values()))
    )
    store = MemoryStore()
    await store.set(SOLVER_HOTSET_STORE_KEY, payload)
    restored = SolverHotSet(max_size=32, persist=True)

    await restored.load(store, now=1)

    assert restored.entries == {}
    assert restored.current_bytes == 0
    assert restored.generation == 1
