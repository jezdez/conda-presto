"""Core resolving logic using conda's solver API.

This module performs dry-run solves: it resolves a set of package specs
against conda channels and returns fully-pinned package metadata
(versions, builds, SHA256 hashes, URLs) without downloading or
installing anything.

Performance notes:
    - ``build_index()`` caches ``RattlerIndexHelper`` objects keyed by
      ``(channels, platform)``.  Building an index (~700 ms) is the
      dominant cost of a solve; the cache reduces repeat solves to just
      the SAT time (~20-100 ms).  ``index_lock`` makes check-then-build
      atomic, preventing thundering-herd on cold or cleared caches.
      Call ``clear_index_cache()`` to invalidate all entries.
    - Multi-platform solves run in a persistent ``ProcessPoolExecutor``
      to bypass the GIL. Workers retain their own index caches across
      requests.
    - All arguments to ``solve_one_platform`` are plain strings/tuples
      so they serialize cheaply for cross-process dispatch.
    - ``ResolvedPackage`` and ``SolveResult`` are ``msgspec.Struct``
      subclasses, which are faster to instantiate and use less memory
      than dataclasses.  Both the HTTP API and the CLI's default
      output encode them directly via ``msgspec.json`` — there is no
      intermediate ``dict`` conversion.  The CLI's ``--format`` path
      feeds conda's exporter plugins with ``Environment`` objects
      (via ``solve_environments``) instead.

Security notes:
    - ``run_solver`` is protected by ``platform_lock`` so that
      concurrent threads (from ``anyio.to_thread``) cannot race on the
      conda ``context`` singleton state.
    - Solver errors are caught and wrapped via
      :func:`conda_presto.exceptions.safe_error_message` so that only
      an allow-list of known exception types surfaces its detail to
      API clients; everything else returns a generic message.  Full
      detail is still logged server-side.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from operator import attrgetter

import msgspec
from conda.base.context import context
from conda.models.environment import Environment
from conda.models.match_spec import MatchSpec
from conda.models.records import PackageRecord
from conda_rattler_solver.index import RattlerIndexHelper
from conda_rattler_solver.state import SolverInputState, SolverOutputState

from .config import (
    GLIBC_VERSION,
    LINUX_VERSION,
    MAX_WORKERS,
    OSX_VERSION,
    WIN_VERSION,
)
from .exceptions import safe_error_message

log = logging.getLogger(__name__)

# Virtual package overrides for cross-platform solving.
# Keyed by platform prefix -> {package_name: version}.
# Versions are configurable via CONDA_PRESTO_GLIBC_VERSION,
# CONDA_PRESTO_LINUX_VERSION, CONDA_PRESTO_OSX_VERSION, and
# CONDA_PRESTO_WIN_VERSION.  ``__win`` is usually unversioned on
# conda-forge; we still provide a value to keep the override dict shape
# consistent across platforms.
VIRTUAL_PACKAGES: dict[str, dict[str, str]] = {
    "linux": {"glibc": GLIBC_VERSION, "linux": LINUX_VERSION},
    "osx": {"osx": OSX_VERSION},
    "win": {"win": WIN_VERSION},
}

NATIVE_SUBDIR: str = context.subdir

current_platform: str | None = None
platform_lock = threading.Lock()
context_configured = False

index_lock = threading.Lock()
index_cache: dict[tuple[tuple[str, ...], str], object] = {}


def configure_platform(platform: str):
    """Point conda's context at *platform* for cross-platform solving.

    Workaround: conda has no public API to re-target ``context.subdir``
    for in-process use without mutating ``os.environ``.  We therefore
    write to ``context._cache_`` directly — that's what
    ``context.__init__(argparse_args=...)`` does internally.  It
    avoids process-global env-var writes (which would not be
    thread-safe under the HTTP server's concurrent requests).

    Callers must hold ``platform_lock`` when calling this function,
    since the context singleton is shared across threads.

    Skips re-initialization when the platform has not changed.
    """
    global current_platform
    if current_platform == platform:
        return

    context._cache_["_subdir"] = platform

    overrides: dict[str, str] = {}
    for prefix, pkgs in VIRTUAL_PACKAGES.items():
        if platform.startswith(prefix):
            overrides = pkgs
            break
    context._cache_["_override_virtual_packages"] = overrides

    current_platform = platform


class ResolvedPackage(msgspec.Struct):
    """A single resolved package with its metadata.

    ``msgspec.Struct`` provides lower memory usage than ``dataclasses``
    and native JSON encoding in Litestar without intermediate dicts.
    ``depends`` and ``constrains`` are stored as tuples (immutable, no
    over-allocation for growth).
    """

    name: str
    version: str
    build: str
    build_number: int
    channel: str
    subdir: str
    url: str
    sha256: str
    md5: str
    size: int | None
    depends: tuple[str, ...]
    constrains: tuple[str, ...]

    @classmethod
    def from_record(cls, record: PackageRecord) -> ResolvedPackage:
        """Convert a conda ``PackageRecord`` to a ``ResolvedPackage``.

        Extracts only the fields needed for the API response.
        ``getattr`` is used for ``size`` because auxlib Entity fields
        raise ``AttributeError`` when unset rather than returning None.
        """
        return cls(
            name=record.name,
            version=str(record.version),
            build=record.build,
            build_number=record.build_number,
            channel=record.channel.canonical_name if record.channel else "",
            subdir=record.subdir or "",
            url=record.url or "",
            sha256=record.sha256 or "",
            md5=record.md5 or "",
            size=getattr(record, "size", None),
            depends=tuple(record.depends) if record.depends else (),
            constrains=tuple(record.constrains) if record.constrains else (),
        )


class SolveResult(msgspec.Struct):
    """The result of a solve operation for a single platform.

    On solver failure, ``packages`` is empty and ``error`` contains a
    sanitized error message (no internal paths or stack traces).
    """

    platform: str
    packages: list[ResolvedPackage]
    error: str | None = None


def configure_context():
    """Set conda context options for fast, quiet solves.

    Sets ``context.json`` (no-op spinner, no progress noise) and
    ensures the rattler solver backend is selected.

    Workaround: conda exposes neither as kwargs to the plugin-invoked
    CLI, so we set them directly on the singleton's cache (same
    approach as :func:`configure_platform`).
    """
    global context_configured
    if context_configured:
        return
    context.json = True
    context._cache_["solver"] = "rattler"
    context_configured = True


def build_index(
    channels: tuple[str, ...],
    platform: str,
) -> object:
    """Return a cached ``RattlerIndexHelper``, building if absent.

    ``index_lock`` makes the check-then-build atomic, so only one
    thread ever builds a given index — no thundering herd.
    """
    key = (channels, platform)
    with index_lock:
        cached = index_cache.get(key)
        if cached is not None:
            return cached
        log.debug("Building index for %s/%s", channels, platform)
        index = RattlerIndexHelper(
            channels=list(channels),
            subdirs=(platform, "noarch"),
        )
        index_cache[key] = index
        return index


def clear_index_cache():
    """Drop all cached indexes."""
    with index_lock:
        index_cache.clear()


def run_solver(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> list[PackageRecord]:
    """Run the conda solver and return raw ``PackageRecord`` objects.

    Holds ``platform_lock`` while configuring platform/context and
    running the solver, because the conda ``context`` singleton is
    shared across threads and the solver reads from it during
    construction and solving.

    Uses a cached ``RattlerIndexHelper`` so that repeated solves for
    the same channels/platform skip the ~700 ms index-build step.

    Returns records sorted by name.  Raises on solver failure so
    callers can handle errors in their own way.

    Workaround: the public ``Solver.solve_final_state`` API loads
    ``PrefixData`` from disk (even for a non-existent prefix), which
    costs ~100 ms per call and has nothing to add for a dry-run.
    We bypass it by driving the conda-rattler-solver
    ``_solving_loop`` directly with a pre-built index.  This reaches
    into the solver plugin's internals, so this call site is the one
    place in conda-presto that couples to a specific solver backend.
    """
    with platform_lock:
        configure_platform(platform)
        configure_context()

        specs = [MatchSpec(dep) for dep in dependencies]

        solver_backend = context.plugin_manager.get_cached_solver_backend()
        if solver_backend is None:
            raise RuntimeError("No solver backend found")

        index = build_index(channels, platform)

        solver = solver_backend(
            prefix="/env/does/not/exist",
            channels=channels or context.channels,
            subdirs=(platform, "noarch"),
            specs_to_add=specs,
            command="create",
        )

        in_state = SolverInputState(
            prefix=solver.prefix,
            requested=solver.specs_to_add,
            command="create",
        )
        out_state = SolverOutputState(solver_input_state=in_state)
        out_state = solver._solving_loop(in_state, out_state, index)
        records = list(out_state.current_solution)

    return sorted(records, key=attrgetter("name"))


def dispatch[T](
    solver_fn: Callable[[tuple[str, ...], list[str], str], T],
    channels: tuple[str, ...],
    dependencies: list[str],
    platforms: list[str],
    *,
    on_error: Callable[[str, Exception], T] | None = None,
) -> list[T]:
    """Dispatch *solver_fn* across *platforms*, preserving order.

    Single-platform solves run in-process; multi-platform solves are
    dispatched to the persistent process pool.  When *on_error* is
    provided, exceptions from individual platforms are caught and
    converted via ``on_error(platform, exc)``; otherwise they
    propagate.
    """
    if len(platforms) == 1:
        if on_error is not None:
            try:
                return [solver_fn(channels, dependencies, platforms[0])]
            except Exception as exc:
                return [on_error(platforms[0], exc)]
        return [solver_fn(channels, dependencies, platforms[0])]

    results: dict[str, T] = {}
    pool = get_process_pool()
    futures = {pool.submit(solver_fn, channels, dependencies, p): p for p in platforms}
    for future in as_completed(futures):
        platform = futures[future]
        try:
            results[platform] = future.result()
        except Exception as exc:
            if on_error is not None:
                results[platform] = on_error(platform, exc)
            else:
                raise

    return [results[p] for p in platforms]


def solve_one_platform(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> SolveResult:
    """Solve for a single platform, returning a ``SolveResult``.

    Used by the HTTP API.  Wraps solver output in lightweight
    ``ResolvedPackage`` objects for fast serialization and low memory.
    """
    try:
        records = run_solver(channels, dependencies, platform)
    except Exception as exc:
        log.warning("Solver error for %s: %s", platform, exc)
        return SolveResult(
            platform=platform, packages=[], error=safe_error_message(exc)
        )

    packages = [ResolvedPackage.from_record(r) for r in records]
    return SolveResult(platform=platform, packages=packages)


def solve_result_error(platform: str, exc: Exception) -> SolveResult:
    """Wrap an exception as a ``SolveResult`` with a sanitized message."""
    log.warning("Solver dispatch error for %s: %s", platform, exc)
    return SolveResult(platform=platform, packages=[], error=safe_error_message(exc))


def solve(
    channels: list[str],
    dependencies: list[str],
    platforms: list[str] | None = None,
) -> list[SolveResult]:
    """Solve for one or more platforms, returning ``SolveResult`` objects.

    Used by the HTTP API.  Single-platform solves run in-process;
    multi-platform solves are dispatched to a persistent process pool.
    Errors are captured per-platform rather than raised.
    """
    return dispatch(
        solve_one_platform,
        tuple(channels),
        dependencies,
        platforms or [NATIVE_SUBDIR],
        on_error=solve_result_error,
    )


def solve_one_environment(
    channels: tuple[str, ...],
    dependencies: list[str],
    platform: str,
) -> Environment:
    """Solve for a single platform, returning a conda ``Environment``.

    Used by the CLI.  Keeps ``PackageRecord`` objects from the solver
    without conversion, so conda's exporter plugins can use them
    directly.  Raises on solver failure.
    """
    records = run_solver(channels, dependencies, platform)
    return Environment(
        platform=platform,
        explicit_packages=records,
    )


def solve_environments(
    channels: list[str],
    dependencies: list[str],
    platforms: list[str] | None = None,
) -> list[Environment]:
    """Solve for one or more platforms, returning ``Environment`` objects.

    Used by the CLI.  Multi-platform solves are dispatched to the
    process pool.  Errors propagate as exceptions.
    """
    return dispatch(
        solve_one_environment,
        tuple(channels),
        dependencies,
        platforms or [NATIVE_SUBDIR],
    )


process_pool: ProcessPoolExecutor | None = None
pool_lock = threading.Lock()


def get_process_pool() -> ProcessPoolExecutor:
    """Return a persistent process pool, creating it on first use.

    Uses double-checked locking to be safe under concurrent requests
    from the async server.  Workers survive across requests so their
    in-memory SubdirData and solver index caches accumulate, making
    repeated solves faster.

    ``max_workers`` defaults to ``min(4, cpu_count)`` and can be
    overridden via ``CONDA_PRESTO_WORKERS``.
    """
    global process_pool
    if process_pool is not None:
        return process_pool
    with pool_lock:
        if process_pool is None:
            process_pool = ProcessPoolExecutor(max_workers=MAX_WORKERS)
        return process_pool


def shutdown_process_pool() -> None:
    """Shut down the process pool if it was started.

    Idempotent; safe to call from Litestar's ``on_shutdown`` hook.
    Uses ``wait=False`` and ``cancel_futures=True`` so shutdown doesn't
    block on in-flight solves during server teardown.
    """
    global process_pool
    with pool_lock:
        if process_pool is not None:
            process_pool.shutdown(wait=False, cancel_futures=True)
            process_pool = None


def warmup_indexes(channels: list[str], platforms: list[str]):
    """Pre-build and cache ``RattlerIndexHelper`` for each platform.

    This does the expensive repodata fetch + parse once so that the
    first real solve request hits the cache and only pays SAT time.
    """
    ch = tuple(channels)
    with platform_lock:
        for platform in platforms:
            configure_platform(platform)
            configure_context()
            build_index(ch, platform)


def warmup(channels: list[str], platforms: list[str]):
    """Pre-warm index caches for both the parent process and worker pool.

    Builds cached indexes in the parent process first, then dispatches
    warmup tasks to each worker process so they build their own
    in-memory index caches.  This ensures the first real solve request
    doesn't pay the full cold-start cost.
    """
    warmup_indexes(channels, platforms)

    pool = get_process_pool()
    futures = [pool.submit(warmup_indexes, channels, [p]) for p in platforms]
    for f in futures:
        f.result()
