"""Scheduled solver cache refresh."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import anyio

from .cache import SolverResultService
from .config import SOLVE_TIMEOUT_S
from .solver import PrestoSolveError
from .warm_candidates import SolverWarmCandidates
from .worker import PersistentSolveWorker

log = logging.getLogger(__name__)

SOLVER_CACHE_WARM_INITIAL_DELAY_S = 30
SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S = 30
SOLVER_CACHE_WARM_CYCLE_BUDGET_S = 60


@dataclass
class ForegroundCapacity:
    """Track foreground arrivals around AnyIO's native limiter."""

    limiter: anyio.CapacityLimiter
    generation: int = 0

    def arrive(self) -> anyio.CapacityLimiter:
        """Record one admission and return the native limiter."""
        self.generation += 1
        return self.limiter

    def idle_generation(self) -> int | None:
        """Return the arrival generation only while no foreground work exists."""
        statistics = self.limiter.statistics()
        if statistics.borrowed_tokens or statistics.tasks_waiting:
            return None
        return self.generation


@dataclass
class SolverCacheWarmStats:
    """Aggregate process-local cache-warming counters."""

    recorded_requests: int = 0
    cycles: int = 0
    already_current: int = 0
    attempts: int = 0
    successful_refreshes: int = 0
    foreground_skips: int = 0
    timeouts: int = 0
    failures: int = 0
    rejected_publications: int = 0


@dataclass
class SolverCacheWarmer:
    """Refresh recorded solver requests in scheduled cycles."""

    warm_candidates: SolverWarmCandidates
    service: SolverResultService
    limiter: ForegroundCapacity
    interval_s: float
    batch_size: int
    thread_limiter: anyio.CapacityLimiter = field(
        default_factory=lambda: anyio.CapacityLimiter(1)
    )
    stats: SolverCacheWarmStats = field(default_factory=SolverCacheWarmStats)
    active_worker: PersistentSolveWorker | None = field(default=None, init=False)

    async def run(self, stop: anyio.Event) -> None:
        """Run non-overlapping cycles until the lifespan signals shutdown."""
        if self.interval_s <= 0 or self.batch_size <= 0:
            return
        delay_s = SOLVER_CACHE_WARM_INITIAL_DELAY_S
        while True:
            with anyio.move_on_after(delay_s):
                await stop.wait()
            if stop.is_set():
                return
            started = time.monotonic()
            try:
                await self.cycle(stop)
            except Exception:
                self.stats.failures += 1
                log.warning("Solver cache refresh cycle failed")
            delay_s = max(0.0, self.interval_s - (time.monotonic() - started))

    async def cycle(self, stop: anyio.Event | None = None) -> None:
        """Refresh the selected cache-warming candidates."""
        started = time.monotonic()
        self.stats.cycles += 1
        limit = self.batch_size
        if self.service.cache.store_operations is None:
            limit = min(limit, self.service.cache.max_size)
        selected = 0
        try:
            candidates = self.warm_candidates.candidates(max(0, limit))
            selected = len(candidates)
            for snapshot in candidates:
                if stop is not None and stop.is_set():
                    break
                if time.monotonic() - started >= SOLVER_CACHE_WARM_CYCLE_BUDGET_S:
                    break
                entry = self.warm_candidates.candidate(snapshot.fingerprint)
                if entry is None:
                    continue
                generation = self.limiter.idle_generation()
                if generation is None:
                    self.stats.foreground_skips += 1
                    break

                remaining_s = SOLVER_CACHE_WARM_CYCLE_BUDGET_S - (
                    time.monotonic() - started
                )
                if remaining_s <= 0:
                    break
                action = "solve"
                try:
                    with anyio.fail_after(
                        min(
                            SOLVE_TIMEOUT_S,
                            SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
                            remaining_s,
                        )
                    ):
                        probe = await self.service.inspect(entry.request)
                except TimeoutError:
                    self.stats.timeouts += 1
                    log.warning(
                        "Solver cache refresh inspection timed out (%s)",
                        entry.fingerprint[:12],
                    )
                    action = "continue"
                else:
                    current = probe.current
                    if current is None:
                        self.stats.failures += 1
                        log.warning(
                            "Solver cache refresh metadata failed (%s)",
                            entry.fingerprint[:12],
                        )
                        action = "continue"
                    elif probe.persistence_failed:
                        self.stats.rejected_publications += 1
                        log.warning(
                            "Solver cache refresh persistence failed (%s)",
                            entry.fingerprint[:12],
                        )
                        action = "continue"
                    elif current.has_local_sources:
                        action = "discard"
                    elif probe.cached:
                        self.stats.already_current += 1
                        action = "continue"

                if action == "discard":
                    self.warm_candidates.discard(entry.fingerprint)
                    if self.limiter.generation != generation:
                        self.stats.foreground_skips += 1
                        break
                    continue
                if self.limiter.generation != generation:
                    self.stats.foreground_skips += 1
                    break
                if action == "continue":
                    continue

                if stop is not None and stop.is_set():
                    break
                if time.monotonic() - started >= SOLVER_CACHE_WARM_CYCLE_BUDGET_S:
                    break
                if self.limiter.idle_generation() != generation:
                    self.stats.foreground_skips += 1
                    break
                if self.active_worker is None:
                    self.active_worker = PersistentSolveWorker(
                        [],
                        [],
                        restart_on_failure=False,
                        startup_timeout_s=min(
                            SOLVE_TIMEOUT_S,
                            SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
                        ),
                        warmup_on_start=False,
                        log_worker_errors=False,
                    )
                    try:
                        await anyio.to_thread.run_sync(
                            self.active_worker.start,
                            limiter=self.thread_limiter,
                        )
                    except TimeoutError:
                        self.stats.timeouts += 1
                        log.warning(
                            "Solver cache refresh worker startup timed out (%s)",
                            entry.fingerprint[:12],
                        )
                        break
                    except Exception:
                        self.stats.failures += 1
                        log.warning(
                            "Solver cache refresh worker startup failed (%s)",
                            entry.fingerprint[:12],
                        )
                        break
                    if self.limiter.idle_generation() != generation:
                        self.stats.foreground_skips += 1
                        break
                    if stop is not None and stop.is_set():
                        break

                remaining_s = SOLVER_CACHE_WARM_CYCLE_BUDGET_S - (
                    time.monotonic() - started
                )
                if remaining_s <= 0:
                    break
                timeout_s = min(
                    SOLVE_TIMEOUT_S,
                    SOLVER_CACHE_WARM_REQUEST_TIMEOUT_S,
                    remaining_s,
                )
                self.stats.attempts += 1
                try:
                    with anyio.fail_after(timeout_s):
                        result = await self.service.resolve(
                            entry.request,
                            self.active_worker,
                            time.monotonic() + timeout_s,
                        )
                except TimeoutError:
                    self.stats.timeouts += 1
                    log.warning(
                        "Solver cache refresh timed out (%s)",
                        entry.fingerprint[:12],
                    )
                    if self.limiter.generation != generation:
                        self.stats.foreground_skips += 1
                        break
                    if not self.active_worker.ready:
                        break
                    continue
                except Exception:
                    self.stats.failures += 1
                    log.warning(
                        "Solver cache refresh failed (%s)",
                        entry.fingerprint[:12],
                    )
                    if self.limiter.generation != generation:
                        self.stats.foreground_skips += 1
                        break
                    if not self.active_worker.ready:
                        break
                    continue

                foreground_arrived = self.limiter.generation != generation
                if isinstance(result.result, PrestoSolveError):
                    self.stats.failures += 1
                    current_entry = self.warm_candidates.candidate(entry.fingerprint)
                    if (
                        current_entry is not None
                        and current_entry.request_count == entry.request_count
                        and current_entry.last_requested == entry.last_requested
                    ):
                        self.warm_candidates.discard(entry.fingerprint)
                    log.warning(
                        "Solver cache refresh solve failed (%s)",
                        entry.fingerprint[:12],
                    )
                elif result.disposition == "published":
                    self.stats.successful_refreshes += 1
                elif result.disposition == "already-current":
                    self.stats.already_current += 1
                    if not foreground_arrived:
                        self.stats.successful_refreshes += 1
                elif result.disposition == "cache-hit":
                    self.stats.already_current += 1
                elif result.disposition == "persistent-failed":
                    self.stats.rejected_publications += 1
                    log.warning(
                        "Solver cache refresh persistence failed (%s)",
                        entry.fingerprint[:12],
                    )
                else:
                    self.stats.rejected_publications += 1
                    log.warning(
                        "Solver cache refresh publication rejected (%s)",
                        entry.fingerprint[:12],
                    )

                if foreground_arrived:
                    self.stats.foreground_skips += 1
                if foreground_arrived or (stop is not None and stop.is_set()):
                    break
        finally:
            with anyio.CancelScope(shield=True):
                if self.active_worker is not None:
                    try:
                        stopped = await anyio.to_thread.run_sync(
                            self.active_worker.shutdown,
                            abandon_on_cancel=True,
                            limiter=self.thread_limiter,
                        )
                        if stopped is not False:
                            self.active_worker = None
                        else:
                            self.stats.failures += 1
                            log.warning(
                                "Solver cache refresh worker cleanup incomplete"
                            )
                    except Exception:
                        self.stats.failures += 1
                        log.warning("Solver cache refresh worker cleanup failed")
                await self.warm_candidates.checkpoint()
                summary = {"selected": selected, **vars(self.stats)}
                log.info(
                    "Solver cache refresh cycle %s",
                    " ".join(f"{key}={value}" for key, value in summary.items()),
                    extra={"solver_cache_refresh": summary},
                )
