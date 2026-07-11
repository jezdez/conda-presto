"""Bounded local demand state for replayable solver requests."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

import anyio
import msgspec
from litestar.stores.base import Store

from .resolve import RepodataSnapshot
from .solver import PrestoSolveRequest

log = logging.getLogger(__name__)

SOLVER_HOTSET_STORE_KEY = "solver-hotset-v1"
SOLVER_HOTSET_MAX_BYTES = 16 * 1024 * 1024
SOLVER_HOTSET_SCORE_HALF_LIFE_S = 24 * 60 * 60
SOLVER_HOTSET_MAX_AGE_S = 7 * 24 * 60 * 60
SOLVER_HOTSET_MIN_OBSERVATIONS = 2
SOLVER_HOTSET_STORE_TIMEOUT_S = 2


class SolverHotSetEntry(msgspec.Struct):
    """One exact replayable workload and its local demand state."""

    fingerprint: str
    request: PrestoSolveRequest
    request_size: int
    score: float
    observations: int
    first_seen: float
    last_seen: float
    last_successful_warm: float | None = None
    failed_repodata_records: (
        tuple[tuple[str, str, int | None, int | None], ...] | None
    ) = None
    retry_at: float = 0.0
    consecutive_transient_failures: int = 0

    def score_at(self, now: float) -> float:
        """Return this entry's exponentially decayed score at *now*."""
        elapsed = max(0.0, now - self.last_seen)
        return self.score * 2 ** (-elapsed / SOLVER_HOTSET_SCORE_HALF_LIFE_S)

    def eligible(self, now: float) -> bool:
        """Return whether this entry has enough recent demand for warming."""
        return (
            self.observations >= SOLVER_HOTSET_MIN_OBSERVATIONS
            and max(0.0, now - self.last_seen) <= SOLVER_HOTSET_MAX_AGE_S
            and self.retry_at <= now
        )


class SolverHotSetCatalog(msgspec.Struct):
    """Versioned persistent representation of a solver hot set."""

    entries: list[SolverHotSetEntry]
    version: Literal[1] = 1


@dataclass
class SolverHotSet:
    """Track, rank, and optionally persist exact successful solver requests."""

    max_size: int
    persist: bool = False
    max_bytes: int = SOLVER_HOTSET_MAX_BYTES
    entries: dict[str, SolverHotSetEntry] = field(default_factory=dict)
    current_bytes: int = 0
    generation: int = 0
    persisted_generation: int = 0
    lock: anyio.Lock = field(default_factory=anyio.Lock)

    async def observe(
        self,
        request: PrestoSolveRequest,
        now: float | None = None,
    ) -> bool:
        """Record one successful foreground request without persistent I/O."""
        if self.max_size == 0:
            return False
        now = time.time() if now is None else now
        fingerprint = request.workload_key()
        request_size = len(msgspec.msgpack.encode(request))
        if request_size > self.max_bytes:
            return False

        async with self.lock:
            self._prune(now)
            if entry := self.entries.get(fingerprint):
                self.current_bytes += request_size - entry.request_size
                entry.request = request
                entry.request_size = request_size
                entry.score = entry.score_at(now) + 1.0
                entry.observations += 1
                entry.last_seen = now
                entry.failed_repodata_records = None
                entry.retry_at = 0.0
                entry.consecutive_transient_failures = 0
            else:
                self.entries[fingerprint] = SolverHotSetEntry(
                    fingerprint=fingerprint,
                    request=request,
                    request_size=request_size,
                    score=1.0,
                    observations=1,
                    first_seen=now,
                    last_seen=now,
                )
                self.current_bytes += request_size
            self.generation += 1
            self._enforce_limits(now)
            return fingerprint in self.entries

    async def candidates(
        self,
        limit: int,
        now: float | None = None,
    ) -> tuple[SolverHotSetEntry, ...]:
        """Return a deterministic snapshot of the hottest eligible entries."""
        now = time.time() if now is None else now
        async with self.lock:
            if self._prune(now):
                self.generation += 1
            return tuple(
                msgspec.structs.replace(entry)
                for entry in self._ranked(now)
                if entry.eligible(now)
            )[:limit]

    async def mark_warm(self, fingerprint: str, now: float | None = None) -> None:
        """Record a successful background refresh without increasing demand."""
        now = time.time() if now is None else now
        async with self.lock:
            if entry := self.entries.get(fingerprint):
                entry.last_successful_warm = now
                entry.failed_repodata_records = None
                entry.retry_at = 0.0
                entry.consecutive_transient_failures = 0
                self.generation += 1

    async def mark_deterministic_failure(
        self,
        fingerprint: str,
        repodata: RepodataSnapshot,
    ) -> None:
        """Remember a solver error for one exact repodata snapshot."""
        async with self.lock:
            if entry := self.entries.get(fingerprint):
                entry.failed_repodata_records = repodata.records
                entry.retry_at = 0.0
                entry.consecutive_transient_failures = 0
                self.generation += 1

    async def mark_transient_failure(
        self,
        fingerprint: str,
        retry_at: float,
    ) -> None:
        """Delay another warm attempt after one transient failure."""
        async with self.lock:
            if entry := self.entries.get(fingerprint):
                entry.failed_repodata_records = None
                entry.retry_at = retry_at
                entry.consecutive_transient_failures += 1
                self.generation += 1

    async def load(self, store: Store | None, now: float | None = None) -> None:
        """Load a valid credential-free catalog without affecting readiness."""
        if not self.persist or store is None:
            return
        try:
            with anyio.move_on_after(SOLVER_HOTSET_STORE_TIMEOUT_S) as scope:
                payload = await store.get(SOLVER_HOTSET_STORE_KEY)
            if scope.cancel_called:
                log.warning("Persistent solver hot-set read timed out")
                return
        except Exception:
            log.warning("Persistent solver hot-set read failed")
            return
        if payload is None:
            return
        try:
            catalog = msgspec.msgpack.decode(payload, type=SolverHotSetCatalog)
        except (msgspec.DecodeError, msgspec.ValidationError):
            log.warning("Ignoring corrupt persistent solver hot set")
            try:
                with anyio.move_on_after(SOLVER_HOTSET_STORE_TIMEOUT_S):
                    await store.delete(SOLVER_HOTSET_STORE_KEY)
            except Exception:
                log.warning("Persistent solver hot-set cleanup failed")
            return

        now = time.time() if now is None else now
        filtered = False
        async with self.lock:
            self.entries.clear()
            self.current_bytes = 0
            for entry in catalog.entries:
                request_size = len(msgspec.msgpack.encode(entry.request))
                fingerprint = entry.request.workload_key()
                if (
                    fingerprint != entry.fingerprint
                    or entry.request.contains_credentials()
                    or max(0.0, now - entry.last_seen) > SOLVER_HOTSET_MAX_AGE_S
                    or request_size > self.max_bytes
                ):
                    filtered = True
                    continue
                entry.request_size = request_size
                existing = self.entries.get(fingerprint)
                if existing is not None and self._rank_key(existing, now) <= (
                    self._rank_key(entry, now)
                ):
                    filtered = True
                    continue
                if existing is not None:
                    self.current_bytes -= existing.request_size
                self.entries[fingerprint] = entry
                self.current_bytes += request_size
            before = (len(self.entries), self.current_bytes)
            self._enforce_limits(now)
            filtered = filtered or before != (len(self.entries), self.current_bytes)
            self.generation = 1 if filtered else 0
            self.persisted_generation = 0

    async def checkpoint(self, store: Store | None, now: float | None = None) -> None:
        """Persist one bounded credential-free catalog outside request handling."""
        if not self.persist or store is None:
            return
        now = time.time() if now is None else now
        async with self.lock:
            if self._prune(now):
                self.generation += 1
            if self.generation == self.persisted_generation:
                return
            generation = self.generation
            payload = msgspec.msgpack.encode(
                SolverHotSetCatalog(
                    entries=[
                        msgspec.structs.replace(entry)
                        for entry in self._ranked(now)
                        if not entry.request.contains_credentials()
                    ]
                )
            )
        try:
            with anyio.move_on_after(SOLVER_HOTSET_STORE_TIMEOUT_S) as scope:
                await store.set(
                    SOLVER_HOTSET_STORE_KEY,
                    payload,
                    expires_in=SOLVER_HOTSET_MAX_AGE_S,
                )
            if scope.cancel_called:
                log.warning("Persistent solver hot-set write timed out")
                return
        except Exception:
            log.warning("Persistent solver hot-set write failed")
            return
        async with self.lock:
            if self.generation == generation:
                self.persisted_generation = generation

    def _prune(self, now: float) -> bool:
        expired = [
            fingerprint
            for fingerprint, entry in self.entries.items()
            if max(0.0, now - entry.last_seen) > SOLVER_HOTSET_MAX_AGE_S
        ]
        for fingerprint in expired:
            self.current_bytes -= self.entries.pop(fingerprint).request_size
        return bool(expired)

    def _enforce_limits(self, now: float) -> None:
        while len(self.entries) > self.max_size or self.current_bytes > self.max_bytes:
            entry = self._ranked(now)[-1]
            self.current_bytes -= self.entries.pop(entry.fingerprint).request_size

    def _ranked(self, now: float) -> list[SolverHotSetEntry]:
        return sorted(
            self.entries.values(),
            key=lambda entry: self._rank_key(entry, now),
        )

    @staticmethod
    def _rank_key(entry: SolverHotSetEntry, now: float) -> tuple[float, float, str]:
        return (-entry.score_at(now), -entry.last_seen, entry.fingerprint)
