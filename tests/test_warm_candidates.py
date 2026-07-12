"""Tests for solver cache-warming candidates."""

from __future__ import annotations

import anyio
import msgspec
import pytest
from conda.models.channel import Channel
from litestar.stores.file import FileStore
from litestar.stores.memory import MemoryStore
from litestar.stores.redis import RedisStore

import conda_presto.solver as solver_module
import conda_presto.warm_candidates as warm_candidates_module
from conda_presto.warm_candidates import (
    SOLVER_WARM_CANDIDATE_MAX_AGE_S,
    SOLVER_WARM_CANDIDATE_STORE_KEY,
    SolverWarmCandidates,
    StoredWarmCandidates,
)


@pytest.fixture()
def solver_request(make_presto_solver_request):
    return make_presto_solver_request(
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
    )


@pytest.fixture(params=["memory", "file", "redis"])
def persistent_store(request, tmp_path):
    if request.param == "memory":
        return MemoryStore()
    if request.param == "file":
        return FileStore(tmp_path / "warm-candidates", create_directories=True)

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
def test_warming_key_covers_request_fields(solver_request, change):
    changed = msgspec.structs.replace(solver_request, **change)

    assert changed.warming_key() != solver_request.warming_key()


def test_warming_key_preserves_effective_channel_order(solver_request):
    first = msgspec.structs.replace(
        solver_request,
        channels=[Channel("conda-forge").dump(), Channel("bioconda").dump()],
    )
    second = msgspec.structs.replace(
        solver_request,
        channels=[Channel("bioconda").dump(), Channel("conda-forge").dump()],
    )

    assert first.warming_key() != second.warming_key()


def test_warming_key_covers_protocol_version(monkeypatch, solver_request):
    first = solver_request.warming_key()
    monkeypatch.setattr(solver_module, "SOLVER_WARMING_ENVELOPE_VERSION", 2)

    assert solver_request.warming_key() != first


def test_warming_key_uses_effective_repodata_filename(monkeypatch, solver_request):
    current = msgspec.structs.replace(
        solver_request,
        repodata_fn="current_repodata.json",
    )
    monkeypatch.setattr(
        solver_module,
        "maybe_ignore_current_repodata",
        lambda _value: "repodata.json",
    )

    assert current.warming_key() == solver_request.warming_key()


def test_warming_key_excludes_dependency_versions(monkeypatch, solver_request):
    monkeypatch.setattr(solver_module, "pkg_version", lambda _name: "one")
    first_key = solver_request.warming_key()
    first_cache = solver_request.cache_key()
    monkeypatch.setattr(solver_module, "pkg_version", lambda _name: "two")

    assert solver_request.warming_key() == first_key
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

    assert request.has_detected_credentials()


def test_public_request_has_no_detected_credentials(solver_request):
    assert not solver_request.has_detected_credentials()


def test_warm_candidates_requires_two_requests(solver_request):
    warm_candidates = SolverWarmCandidates(max_size=32)

    assert warm_candidates.record(solver_request, now=10)
    assert warm_candidates.candidates(limit=8, now=10) == ()
    assert warm_candidates.record(solver_request, now=20)

    candidates = warm_candidates.candidates(limit=8, now=20)
    assert len(candidates) == 1
    assert candidates[0].request_count == 2
    assert candidates[0].last_requested == 20


def test_warm_candidates_expires_entries_after_seven_days(solver_request):
    warm_candidates = SolverWarmCandidates(max_size=32)
    warm_candidates.record(solver_request, now=0)
    warm_candidates.record(solver_request, now=1)

    candidates = warm_candidates.candidates(
        limit=8,
        now=SOLVER_WARM_CANDIDATE_MAX_AGE_S + 2,
    )

    assert candidates == ()
    assert warm_candidates.entries == {}


def test_warm_candidates_ranks_by_request_count_then_recency(solver_request):
    warm_candidates = SolverWarmCandidates(max_size=32)
    older = msgspec.structs.replace(solver_request, specs_to_add=["older"])
    newer = msgspec.structs.replace(solver_request, specs_to_add=["newer"])
    most_requested = msgspec.structs.replace(
        solver_request,
        specs_to_add=["most-requested"],
    )
    for request in (older, newer, most_requested):
        warm_candidates.record(request, now=10)
        warm_candidates.record(request, now=10)
    warm_candidates.record(most_requested, now=10)
    warm_candidates.record(most_requested, now=10)
    warm_candidates.record(newer, now=20)

    candidates = warm_candidates.candidates(limit=3, now=20)

    assert [candidate.fingerprint for candidate in candidates] == [
        most_requested.warming_key(),
        newer.warming_key(),
        older.warming_key(),
    ]


def test_warm_candidates_uses_fingerprint_as_final_ranking_tie_breaker(solver_request):
    warm_candidates = SolverWarmCandidates(max_size=32)
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("alpha", "bravo")
    ]
    for request in requests:
        warm_candidates.record(request, now=10)
        warm_candidates.record(request, now=10)

    candidates = warm_candidates.candidates(limit=2, now=10)

    assert [candidate.fingerprint for candidate in candidates] == sorted(
        request.warming_key() for request in requests
    )


def test_warm_candidates_evicts_lowest_ranked_entry_at_count_limit(solver_request):
    warm_candidates = SolverWarmCandidates(max_size=2)
    most_requested = msgspec.structs.replace(
        solver_request,
        specs_to_add=["most-requested"],
    )
    oldest = msgspec.structs.replace(solver_request, specs_to_add=["oldest"])
    newest = msgspec.structs.replace(solver_request, specs_to_add=["newest"])
    warm_candidates.record(most_requested, now=0)
    warm_candidates.record(most_requested, now=0)
    warm_candidates.record(oldest, now=1)
    warm_candidates.record(newest, now=2)

    assert set(warm_candidates.entries) == {
        most_requested.warming_key(),
        newest.warming_key(),
    }


def test_zero_size_disables_recording(solver_request):
    warm_candidates = SolverWarmCandidates(max_size=0)

    assert not warm_candidates.record(solver_request, now=1)
    assert warm_candidates.entries == {}


@pytest.mark.anyio
async def test_persistent_warm_candidates_round_trip(
    persistent_store,
    solver_request,
):
    source = SolverWarmCandidates(max_size=32, persist=True)
    source.record(solver_request, now=10)
    source.record(solver_request, now=20)

    await source.checkpoint(persistent_store, now=20)
    restored = SolverWarmCandidates(max_size=32, persist=True)
    await restored.load(persistent_store, now=30)

    fingerprint = solver_request.warming_key()
    assert list(restored.entries) == [fingerprint]
    assert restored.entries[fingerprint].request == solver_request
    assert restored.entries[fingerprint].request_count == 2
    assert len(restored.candidates(limit=1, now=30)) == 1


@pytest.mark.anyio
async def test_persistence_is_opt_in(solver_request):
    store = MemoryStore()
    source = SolverWarmCandidates(max_size=32)
    source.record(solver_request, now=1)
    await source.checkpoint(store, now=1)
    assert await store.get(SOLVER_WARM_CANDIDATE_STORE_KEY) is None

    payload = msgspec.msgpack.encode(
        StoredWarmCandidates(entries=list(source.entries.values()))
    )
    await store.set(SOLVER_WARM_CANDIDATE_STORE_KEY, payload)
    restored = SolverWarmCandidates(max_size=32)
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
    await store.set(SOLVER_WARM_CANDIDATE_STORE_KEY, payload)
    warm_candidates = SolverWarmCandidates(max_size=32, persist=True)

    await warm_candidates.load(store, now=1)

    assert warm_candidates.entries == {}
    assert await store.get(SOLVER_WARM_CANDIDATE_STORE_KEY) is None


@pytest.mark.anyio
async def test_load_filters_expired_entries(solver_request):
    store = MemoryStore()
    source = SolverWarmCandidates(max_size=32, persist=True)
    source.record(solver_request, now=0)
    await source.checkpoint(store, now=0)
    restored = SolverWarmCandidates(max_size=32, persist=True)

    await restored.load(store, now=SOLVER_WARM_CANDIDATE_MAX_AGE_S + 1)

    assert restored.entries == {}
    assert restored.generation == 1


@pytest.mark.anyio
async def test_load_enforces_local_count_limit(solver_request):
    store = MemoryStore()
    source = SolverWarmCandidates(max_size=32, persist=True)
    requests = [
        msgspec.structs.replace(solver_request, specs_to_add=[name])
        for name in ("once", "twice", "three-times")
    ]
    for request_count, request in enumerate(requests, start=1):
        for _ in range(request_count):
            source.record(request, now=10)
    await source.checkpoint(store, now=10)
    restored = SolverWarmCandidates(max_size=1, persist=True)

    await restored.load(store, now=10)

    assert list(restored.entries) == [requests[-1].warming_key()]


@pytest.mark.anyio
async def test_store_failures_are_best_effort(solver_request):
    class FailingStore:
        async def get(self, _key):
            raise OSError("read failed")

        async def set(self, _key, _value, *, expires_in=None):
            raise OSError("write failed")

        async def delete(self, _key):
            raise OSError("delete failed")

    warm_candidates = SolverWarmCandidates(max_size=32, persist=True)
    warm_candidates.record(solver_request, now=1)

    await warm_candidates.load(FailingStore(), now=1)
    await warm_candidates.checkpoint(FailingStore(), now=1)

    assert solver_request.warming_key() in warm_candidates.entries
    assert warm_candidates.persisted_generation == 0


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["load", "checkpoint"])
async def test_store_timeouts_are_best_effort(monkeypatch, solver_request, operation):
    class SlowStore:
        async def get(self, _key):
            await anyio.sleep_forever()

        async def set(self, _key, _value, *, expires_in=None):
            await anyio.sleep_forever()

    monkeypatch.setattr(
        warm_candidates_module, "SOLVER_WARM_CANDIDATE_STORE_TIMEOUT_S", 0
    )
    warm_candidates = SolverWarmCandidates(max_size=32, persist=True)
    warm_candidates.record(solver_request, now=1)

    await getattr(warm_candidates, operation)(SlowStore(), now=1)

    assert solver_request.warming_key() in warm_candidates.entries
    assert warm_candidates.persisted_generation == 0


@pytest.mark.anyio
async def test_entries_with_detected_credentials_remain_memory_only(solver_request):
    request = msgspec.structs.replace(
        solver_request,
        channels=[{**Channel("conda-forge").dump(), "token": "secret"}],
    )
    warm_candidates = SolverWarmCandidates(max_size=32, persist=True)
    warm_candidates.record(request, now=1)
    warm_candidates.record(request, now=2)
    store = MemoryStore()

    await warm_candidates.checkpoint(store, now=2)

    fingerprint = request.warming_key()
    assert fingerprint in warm_candidates.entries
    assert len(warm_candidates.candidates(limit=1, now=2)) == 1
    payload = await store.get(SOLVER_WARM_CANDIDATE_STORE_KEY)
    catalog = msgspec.msgpack.decode(payload, type=StoredWarmCandidates)
    assert catalog.entries == []


@pytest.mark.anyio
async def test_load_rejects_entry_with_detected_credentials(solver_request):
    request = msgspec.structs.replace(
        solver_request,
        installed=[
            {
                "name": "python",
                "url": "https://repo.example/t/secret/linux-64/python.conda",
            }
        ],
    )
    source = SolverWarmCandidates(max_size=32)
    source.record(request, now=1)
    payload = msgspec.msgpack.encode(
        StoredWarmCandidates(entries=list(source.entries.values()))
    )
    store = MemoryStore()
    await store.set(SOLVER_WARM_CANDIDATE_STORE_KEY, payload)
    restored = SolverWarmCandidates(max_size=32, persist=True)

    await restored.load(store, now=1)

    assert restored.entries == {}
    assert restored.generation == 1
