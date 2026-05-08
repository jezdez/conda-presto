"""Tests for conda_presto.cache (result cache and request hashing)."""

from __future__ import annotations

import threading
import time

import pytest

from conda_presto.cache import CacheEntry, ResultCache, canonical_request_hash


def test_get_empty_cache_returns_none():
    cache = ResultCache(max_entries=10)
    assert cache.get("nonexistent") is None


def test_put_and_get_round_trip():
    cache = ResultCache(max_entries=10)
    entry = CacheEntry(
        body='{"ok": true}',
        media_type="application/json",
        created_at=time.time(),
    )
    cache.put("abc123", entry)
    assert cache.get("abc123") is entry


def test_lru_eviction():
    cache = ResultCache(max_entries=2)
    e1 = CacheEntry(body="one", media_type="text/plain", created_at=1.0)
    e2 = CacheEntry(body="two", media_type="text/plain", created_at=2.0)
    e3 = CacheEntry(body="three", media_type="text/plain", created_at=3.0)

    cache.put("k1", e1)
    cache.put("k2", e2)
    assert len(cache) == 2

    cache.put("k3", e3)
    assert len(cache) == 2
    assert cache.get("k1") is None
    assert cache.get("k2") is e2
    assert cache.get("k3") is e3


def test_lru_access_promotes_entry():
    cache = ResultCache(max_entries=2)
    e1 = CacheEntry(body="one", media_type="text/plain", created_at=1.0)
    e2 = CacheEntry(body="two", media_type="text/plain", created_at=2.0)
    e3 = CacheEntry(body="three", media_type="text/plain", created_at=3.0)

    cache.put("k1", e1)
    cache.put("k2", e2)

    cache.get("k1")

    cache.put("k3", e3)
    assert cache.get("k1") is e1
    assert cache.get("k2") is None
    assert cache.get("k3") is e3


def test_thread_safety():
    cache = ResultCache(max_entries=500)
    errors: list[Exception] = []

    def writer(start: int) -> None:
        try:
            for i in range(100):
                key = f"key-{start}-{i}"
                entry = CacheEntry(
                    body=f"body-{start}-{i}",
                    media_type="text/plain",
                    created_at=time.time(),
                )
                cache.put(key, entry)
                cache.get(key)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(cache) <= 500


def test_len_empty():
    assert len(ResultCache(max_entries=5)) == 0


def test_len_after_puts():
    cache = ResultCache(max_entries=10)
    for i in range(3):
        cache.put(
            f"k{i}",
            CacheEntry(body=str(i), media_type="text/plain", created_at=0.0),
        )
    assert len(cache) == 3


def test_canonical_hash_same_for_equivalent_requests():
    h1 = canonical_request_hash(
        specs=["numpy", "python=3.12"],
        channels=["conda-forge", "defaults"],
        platforms=["linux-64", "osx-arm64"],
        format_name="explicit",
    )
    h2 = canonical_request_hash(
        specs=["python=3.12", "numpy"],
        channels=["defaults", "conda-forge"],
        platforms=["osx-arm64", "linux-64"],
        format_name="explicit",
    )
    assert h1 == h2


def test_canonical_hash_different_for_different_requests():
    h1 = canonical_request_hash(
        specs=["numpy"],
        channels=["conda-forge"],
        platforms=["linux-64"],
        format_name=None,
    )
    h2 = canonical_request_hash(
        specs=["pandas"],
        channels=["conda-forge"],
        platforms=["linux-64"],
        format_name=None,
    )
    assert h1 != h2


def test_canonical_hash_different_format_names():
    h1 = canonical_request_hash(
        specs=["zlib"],
        channels=["conda-forge"],
        platforms=["linux-64"],
        format_name="explicit",
    )
    h2 = canonical_request_hash(
        specs=["zlib"],
        channels=["conda-forge"],
        platforms=["linux-64"],
        format_name="environment-yaml",
    )
    assert h1 != h2


def test_canonical_hash_with_file_content():
    h1 = canonical_request_hash(
        specs=[],
        channels=["conda-forge"],
        platforms=None,
        format_name=None,
        file_content="dependencies:\n  - numpy  \n",
        filename="environment.yml",
    )
    h2 = canonical_request_hash(
        specs=[],
        channels=["conda-forge"],
        platforms=None,
        format_name=None,
        file_content="dependencies:\n  - numpy\n",
        filename="environment.yml",
    )
    assert h1 == h2


@pytest.mark.parametrize(
    "platforms_a, platforms_b",
    [
        pytest.param(None, ["linux-64"], id="none-vs-list"),
        pytest.param(["linux-64"], ["osx-arm64"], id="different-platforms"),
    ],
)
def test_canonical_hash_different_platforms(platforms_a, platforms_b):
    h1 = canonical_request_hash(
        specs=["zlib"],
        channels=["conda-forge"],
        platforms=platforms_a,
        format_name=None,
    )
    h2 = canonical_request_hash(
        specs=["zlib"],
        channels=["conda-forge"],
        platforms=platforms_b,
        format_name=None,
    )
    assert h1 != h2
