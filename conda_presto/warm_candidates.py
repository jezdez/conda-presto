"""Recorded solver requests eligible for cache warming."""

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

SOLVER_WARM_CANDIDATE_STORE_KEY = "solver-warm-candidates-v1"
SOLVER_WARM_CANDIDATE_MAX_BYTES = 16 * 1024 * 1024
SOLVER_WARM_CANDIDATE_SCORE_HALF_LIFE_S = 24 * 60 * 60
SOLVER_WARM_CANDIDATE_MAX_AGE_S = 7 * 24 * 60 * 60
SOLVER_WARM_CANDIDATE_MIN_REQUESTS = 2
SOLVER_WARM_CANDIDATE_STORE_TIMEOUT_S = 2


class SolverWarmCandidate(msgspec.Struct):
    """One recorded solver request and its warming state."""

    fingerprint: str
    request: PrestoSolveRequest
    request_size: int
    score: float
    request_count: int
    last_requested: float
    failed_repodata_records: (
        tuple[tuple[str, str, int | None, int | None], ...] | None
    ) = None
    retry_at: float = 0.0
    consecutive_transient_failures: int = 0

    def score_at(self, now: float) -> float:
        """Return this entry's exponentially decayed score at *now*."""
        elapsed = max(0.0, now - self.last_requested)
        return self.score * 2 ** (-elapsed / SOLVER_WARM_CANDIDATE_SCORE_HALF_LIFE_S)

    def eligible(self, now: float) -> bool:
        """Return whether this request is eligible for warming."""
        return (
            self.request_count >= SOLVER_WARM_CANDIDATE_MIN_REQUESTS
            and max(0.0, now - self.last_requested) <= SOLVER_WARM_CANDIDATE_MAX_AGE_S
            and self.retry_at <= now
        )


class StoredWarmCandidates(msgspec.Struct):
    """Versioned persistent cache-warming candidates."""

    entries: list[SolverWarmCandidate]
    version: Literal[1] = 1


@dataclass
class SolverWarmCandidates:
    """Record successful solver requests for cache warming."""

    max_size: int
    persist: bool = False
    max_bytes: int = SOLVER_WARM_CANDIDATE_MAX_BYTES
    entries: dict[str, SolverWarmCandidate] = field(default_factory=dict)
    current_bytes: int = 0
    generation: int = 0
    persisted_generation: int = 0

    def record(
        self,
        request: PrestoSolveRequest,
        now: float | None = None,
    ) -> bool:
        """Record one successful foreground request without persistent I/O."""
        if self.max_size == 0:
            return False
        now = time.time() if now is None else now
        fingerprint = request.warming_key()
        request_size = len(msgspec.msgpack.encode(request))
        if request_size > self.max_bytes:
            return False

        self._prune(now)
        if entry := self.entries.get(fingerprint):
            self.current_bytes += request_size - entry.request_size
            entry.request = request
            entry.request_size = request_size
            entry.score = entry.score_at(now) + 1.0
            entry.request_count += 1
            entry.last_requested = now
            entry.failed_repodata_records = None
            entry.retry_at = 0.0
            entry.consecutive_transient_failures = 0
        else:
            self.entries[fingerprint] = SolverWarmCandidate(
                fingerprint=fingerprint,
                request=request,
                request_size=request_size,
                score=1.0,
                request_count=1,
                last_requested=now,
            )
            self.current_bytes += request_size
        self.generation += 1
        self._enforce_limits(now)
        return fingerprint in self.entries

    def candidates(
        self,
        limit: int,
        now: float | None = None,
    ) -> tuple[SolverWarmCandidate, ...]:
        """Return eligible candidates in score order."""
        now = time.time() if now is None else now
        if self._prune(now):
            self.generation += 1
        return tuple(
            msgspec.structs.replace(entry)
            for entry in self._ranked(now)
            if entry.eligible(now)
        )[:limit]

    def mark_warm(self, fingerprint: str) -> None:
        """Clear failure backoff after a successful background refresh."""
        if entry := self.entries.get(fingerprint):
            entry.failed_repodata_records = None
            entry.retry_at = 0.0
            entry.consecutive_transient_failures = 0
            self.generation += 1

    def mark_deterministic_failure(
        self,
        fingerprint: str,
        repodata: RepodataSnapshot,
    ) -> None:
        """Remember a solver error for one set of repodata markers."""
        if entry := self.entries.get(fingerprint):
            entry.failed_repodata_records = repodata.records
            entry.retry_at = 0.0
            entry.consecutive_transient_failures = 0
            self.generation += 1

    def mark_transient_failure(
        self,
        fingerprint: str,
        retry_at: float,
    ) -> None:
        """Delay another warm attempt after one transient failure."""
        if entry := self.entries.get(fingerprint):
            entry.failed_repodata_records = None
            entry.retry_at = retry_at
            entry.consecutive_transient_failures += 1
            self.generation += 1

    async def load(self, store: Store | None, now: float | None = None) -> None:
        """Load persisted candidates without affecting readiness."""
        if not self.persist or store is None:
            return
        try:
            with anyio.move_on_after(SOLVER_WARM_CANDIDATE_STORE_TIMEOUT_S) as scope:
                payload = await store.get(SOLVER_WARM_CANDIDATE_STORE_KEY)
            if scope.cancel_called:
                log.warning("Cache-warming candidate read timed out")
                return
        except Exception:
            log.warning("Cache-warming candidate read failed")
            return
        if payload is None:
            return
        try:
            catalog = msgspec.msgpack.decode(payload, type=StoredWarmCandidates)
        except (msgspec.DecodeError, msgspec.ValidationError):
            log.warning("Ignoring corrupt cache-warming candidates")
            try:
                with anyio.move_on_after(SOLVER_WARM_CANDIDATE_STORE_TIMEOUT_S):
                    await store.delete(SOLVER_WARM_CANDIDATE_STORE_KEY)
            except Exception:
                log.warning("Cache-warming candidate cleanup failed")
            return

        now = time.time() if now is None else now
        filtered = False
        self.entries.clear()
        self.current_bytes = 0
        for entry in catalog.entries:
            request_size = len(msgspec.msgpack.encode(entry.request))
            fingerprint = entry.request.warming_key()
            if (
                fingerprint != entry.fingerprint
                or entry.request.has_detected_credentials()
                or max(0.0, now - entry.last_requested)
                > SOLVER_WARM_CANDIDATE_MAX_AGE_S
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
        """Persist candidates without detected credentials."""
        if not self.persist or store is None:
            return
        now = time.time() if now is None else now
        if self._prune(now):
            self.generation += 1
        if self.generation == self.persisted_generation:
            return
        generation = self.generation
        payload = msgspec.msgpack.encode(
            StoredWarmCandidates(
                entries=[
                    msgspec.structs.replace(entry)
                    for entry in self._ranked(now)
                    if not entry.request.has_detected_credentials()
                ]
            )
        )
        try:
            with anyio.move_on_after(SOLVER_WARM_CANDIDATE_STORE_TIMEOUT_S) as scope:
                await store.set(
                    SOLVER_WARM_CANDIDATE_STORE_KEY,
                    payload,
                    expires_in=SOLVER_WARM_CANDIDATE_MAX_AGE_S,
                )
            if scope.cancel_called:
                log.warning("Cache-warming candidate write timed out")
                return
        except Exception:
            log.warning("Cache-warming candidate write failed")
            return
        if self.generation == generation:
            self.persisted_generation = generation

    def _prune(self, now: float) -> bool:
        expired = [
            fingerprint
            for fingerprint, entry in self.entries.items()
            if max(0.0, now - entry.last_requested) > SOLVER_WARM_CANDIDATE_MAX_AGE_S
        ]
        for fingerprint in expired:
            self.current_bytes -= self.entries.pop(fingerprint).request_size
        return bool(expired)

    def _enforce_limits(self, now: float) -> None:
        while len(self.entries) > self.max_size or self.current_bytes > self.max_bytes:
            entry = self._ranked(now)[-1]
            self.current_bytes -= self.entries.pop(entry.fingerprint).request_size

    def _ranked(self, now: float) -> list[SolverWarmCandidate]:
        return sorted(
            self.entries.values(),
            key=lambda entry: self._rank_key(entry, now),
        )

    @staticmethod
    def _rank_key(entry: SolverWarmCandidate, now: float) -> tuple[float, float, str]:
        return (-entry.score_at(now), -entry.last_requested, entry.fingerprint)
