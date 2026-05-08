"""Thread-safe LRU result cache for solve/transcode results.

Keyed by SHA-256 of the canonicalized request.  Entries are evicted
LRU-first when *max_entries* is exceeded.

Configuration:
    ``CONDA_PRESTO_RESULT_CACHE_MAX_ENTRIES``
        Maximum number of cached results (default: ``1000``).
    ``CONDA_PRESTO_RESULT_CACHE``
        Set to ``false`` (or ``0`` / ``no``) to disable caching entirely
        (default: ``true``).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True)
class CacheEntry:
    """A single cached solve result."""

    body: str
    media_type: str
    created_at: float


class ResultCache:
    """Thread-safe LRU cache for solve/transcode results.

    Keyed by SHA-256 of the canonicalized request.  Entries are
    evicted LRU-first when *max_entries* is exceeded.
    """

    def __init__(self, max_entries: int = 1000) -> None:
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get(self, key: str) -> CacheEntry | None:
        """Return the entry for *key*, promoting it to most-recent."""
        with self._lock:
            if key not in self._store:
                return None
            self._store.move_to_end(key)
            return self._store[key]

    def put(self, key: str, entry: CacheEntry) -> None:
        """Store *entry* under *key*, evicting the oldest if full."""
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
                self._store[key] = entry
            else:
                self._store[key] = entry
                while len(self._store) > self._max_entries:
                    self._store.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


def canonical_request_hash(
    specs: list[str],
    channels: list[str],
    platforms: list[str] | None,
    format_name: str | None,
    file_content: str | None = None,
    filename: str | None = None,
) -> str:
    """SHA-256 of a canonicalized request.

    Canonicalization rules:
    - Sort specs, channels, and platforms so ordering doesn't matter.
    - Normalize whitespace in file content (strip trailing per-line).
    - Include format name and filename.
    - Deterministic JSON encoding (``sort_keys=True``).
    """
    normalized_file: str | None = None
    if file_content is not None:
        normalized_file = "\n".join(line.rstrip() for line in file_content.splitlines())

    payload = {
        "specs": sorted(specs),
        "channels": sorted(channels),
        "platforms": sorted(platforms) if platforms else None,
        "format": format_name,
        "file": normalized_file,
        "filename": filename,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()
