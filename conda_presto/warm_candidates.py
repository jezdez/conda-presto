"""Recorded solver requests eligible for cache warming."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

import anyio
import msgspec
from litestar.stores.base import Store

from .solver import PrestoSolveRequest

log = logging.getLogger(__name__)

SOLVER_WARM_CANDIDATE_STORE_KEY = "solver-warm-candidates-v1"
SOLVER_WARM_CANDIDATE_MAX_AGE_S = 7 * 24 * 60 * 60
SOLVER_WARM_CANDIDATE_MIN_REQUESTS = 2
SOLVER_WARM_CANDIDATE_STORE_TIMEOUT_S = 2


class SolverWarmCandidate(msgspec.Struct):
    """One recorded solver request eligible for cache warming."""

    fingerprint: str
    request: PrestoSolveRequest
    request_count: int
    last_requested: float

    def eligible(self, now: float) -> bool:
        """Return whether this request is eligible for warming."""
        return (
            self.request_count >= SOLVER_WARM_CANDIDATE_MIN_REQUESTS
            and max(0.0, now - self.last_requested) <= SOLVER_WARM_CANDIDATE_MAX_AGE_S
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
    entries: dict[str, SolverWarmCandidate] = field(default_factory=dict)
    observations: dict[str, SolverWarmCandidate] = field(default_factory=dict)
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

        self._prune(now)
        if entry := self.entries.get(fingerprint):
            entry.request = request
            entry.request_count += 1
            entry.last_requested = now
        elif entry := self.observations.pop(fingerprint, None):
            entry.request = request
            entry.request_count += 1
            entry.last_requested = now
            if len(self.entries) == self.max_size:
                del self.entries[self._ranked()[-1].fingerprint]
            self.entries[fingerprint] = entry
        elif len(self.entries) < self.max_size:
            self.entries[fingerprint] = SolverWarmCandidate(
                fingerprint=fingerprint,
                request=request,
                request_count=1,
                last_requested=now,
            )
        else:
            self.observations[fingerprint] = SolverWarmCandidate(
                fingerprint=fingerprint,
                request=request,
                request_count=1,
                last_requested=now,
            )
            self._enforce_observation_limit()
        self.generation += 1
        return fingerprint in self.entries or fingerprint in self.observations

    def candidates(
        self,
        limit: int,
        now: float | None = None,
    ) -> tuple[SolverWarmCandidate, ...]:
        """Return eligible candidates in request-count order."""
        now = time.time() if now is None else now
        if self._prune(now):
            self.generation += 1
        return tuple(
            msgspec.structs.replace(entry)
            for entry in self._ranked()
            if entry.eligible(now)
        )[:limit]

    def discard(self, fingerprint: str) -> None:
        """Discard one request that cannot produce a cacheable result."""
        removed = self.entries.pop(fingerprint, None)
        removed = self.observations.pop(fingerprint, None) or removed
        if removed is not None:
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
        self.observations.clear()
        loaded: dict[str, SolverWarmCandidate] = {}
        for entry in catalog.entries:
            fingerprint = entry.request.warming_key()
            if (
                fingerprint != entry.fingerprint
                or entry.request.has_detected_credentials()
                or max(0.0, now - entry.last_requested)
                > SOLVER_WARM_CANDIDATE_MAX_AGE_S
            ):
                filtered = True
                continue
            existing = loaded.get(fingerprint)
            if existing is not None:
                filtered = True
                if self._rank_key(existing) <= self._rank_key(entry):
                    continue
            loaded[fingerprint] = entry
        ranked = sorted(loaded.values(), key=self._rank_key)
        for entry in ranked:
            target = (
                self.entries
                if entry.eligible(now) or len(self.entries) < self.max_size
                else self.observations
            )
            target[entry.fingerprint] = entry
        before = len(self.entries) + len(self.observations)
        self._enforce_limit()
        self._enforce_observation_limit()
        filtered = filtered or before != len(self.entries) + len(self.observations)
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
                    for entry in (*self._ranked(), *self._ranked_observations())
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
        changed = False
        for entries in (self.entries, self.observations):
            expired = [
                fingerprint
                for fingerprint, entry in entries.items()
                if max(0.0, now - entry.last_requested)
                > SOLVER_WARM_CANDIDATE_MAX_AGE_S
            ]
            for fingerprint in expired:
                del entries[fingerprint]
            changed = changed or bool(expired)
        return changed

    def _enforce_limit(self) -> None:
        while len(self.entries) > self.max_size:
            del self.entries[self._ranked()[-1].fingerprint]

    def _enforce_observation_limit(self) -> None:
        while len(self.observations) > self.max_size:
            del self.observations[self._ranked_observations()[-1].fingerprint]

    def _ranked(self) -> list[SolverWarmCandidate]:
        return sorted(
            self.entries.values(),
            key=self._rank_key,
        )

    def _ranked_observations(self) -> list[SolverWarmCandidate]:
        return sorted(
            self.observations.values(),
            key=lambda entry: (-entry.last_requested, entry.fingerprint),
        )

    @staticmethod
    def _rank_key(entry: SolverWarmCandidate) -> tuple[int, float, str]:
        return (-entry.request_count, -entry.last_requested, entry.fingerprint)
