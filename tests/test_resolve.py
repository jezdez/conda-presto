"""Tests for conda_presto.resolve."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor

import msgspec
import pytest
from conda.base.context import context
from conda.exceptions import PackagesNotFoundError, UnsatisfiableError
from conda.models.environment import Environment
from conda.models.records import PackageRecord

import conda_presto.resolve as resolve_module
from conda_presto.resolve import (
    ResolvedPackage,
    SolveResult,
    build_index,
    clear_index_cache,
    configure_context,
    configure_platform,
    dispatch,
    get_process_pool,
    index_cache,
    platform_lock,
    run_solver,
    shutdown_process_pool,
    solve,
    solve_environments,
    solve_one_platform,
    solve_result_error,
    warmup,
    warmup_indexes,
)


@pytest.fixture()
def _warm_index():
    """Prepare a clean index cache with platform/context configured."""
    clear_index_cache()
    with platform_lock:
        configure_platform("linux-64")
        configure_context()
        yield
    clear_index_cache()


@pytest.mark.parametrize(
    "record_fixture, field, expected",
    [
        pytest.param("sample_record", "name", "zlib", id="name"),
        pytest.param("sample_record", "version", "1.3.1", id="version"),
        pytest.param("sample_record", "build", "h68df207_2", id="build"),
        pytest.param("sample_record", "build_number", 2, id="build_number"),
        pytest.param("sample_record", "sha256", "abc123", id="sha256"),
        pytest.param("sample_record", "md5", "def456", id="md5"),
        pytest.param("sample_record", "size", 102400, id="size"),
        pytest.param("minimal_record", "sha256", "", id="empty-sha256"),
        pytest.param("minimal_record", "md5", "", id="empty-md5"),
        pytest.param("minimal_record", "size", None, id="empty-size"),
        pytest.param("minimal_record", "depends", (), id="empty-depends"),
        pytest.param("minimal_record", "constrains", (), id="empty-constrains"),
    ],
)
def test_resolved_package_from_record(record_fixture, field, expected, request):
    record = request.getfixturevalue(record_fixture)
    pkg = ResolvedPackage.from_record(record)
    assert getattr(pkg, field) == expected


def test_solve_result_msgspec_json_roundtrip(sample_resolved_package):
    """``list[SolveResult]`` serializes via msgspec.json to the shape
    consumed by both the CLI default output and the HTTP API."""
    result = SolveResult(
        platform="linux-64", packages=[sample_resolved_package], error=None
    )
    encoded = msgspec.json.encode([result])
    decoded = msgspec.json.decode(encoded)
    assert isinstance(decoded, list) and len(decoded) == 1
    assert decoded[0]["platform"] == "linux-64"
    assert decoded[0]["error"] is None
    pkg = decoded[0]["packages"][0]
    assert pkg["name"] == "zlib"
    assert isinstance(pkg["depends"], list)
    assert set(pkg.keys()) == {
        "name",
        "version",
        "build",
        "build_number",
        "channel",
        "subdir",
        "url",
        "sha256",
        "md5",
        "size",
        "depends",
        "constrains",
    }


def test_solve_result_error_serializes(sample_resolved_package):
    result = SolveResult(platform="linux-64", packages=[], error="solver failed")
    decoded = msgspec.json.decode(msgspec.json.encode(result))
    assert decoded["error"] == "solver failed"
    assert decoded["packages"] == []


def test_configure_context_sets_json():
    configure_context()
    assert context.json is True


def test_get_process_pool():
    pool1 = get_process_pool()
    pool2 = get_process_pool()
    assert isinstance(pool1, ProcessPoolExecutor)
    assert pool1 is pool2


def test_get_process_pool_threadsafe():
    pools = []

    def grab():
        pools.append(get_process_pool())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(p is pools[0] for p in pools)


def test_run_solver_returns_sorted_records():
    records = run_solver(
        channels=("conda-forge",),
        dependencies=["zlib"],
        platform="linux-64",
    )
    assert all(isinstance(r, PackageRecord) for r in records)
    names = [r.name for r in records]
    assert "zlib" in names
    assert names == sorted(names)


def test_run_solver_unsatisfiable():
    with pytest.raises((UnsatisfiableError, PackagesNotFoundError)):
        run_solver(
            channels=("conda-forge",),
            dependencies=["__nonexistent_package_xyz__"],
            platform="linux-64",
        )


@pytest.mark.parametrize(
    "deps",
    [
        pytest.param(["zlib"], id="single-dep"),
        pytest.param(["python=3.12", "zlib"], id="multi-dep"),
    ],
)
def test_solve_one_platform(deps):
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=deps,
        platform="linux-64",
    )
    assert isinstance(result, SolveResult)
    assert result.platform == "linux-64"
    assert result.error is None
    names = [p.name for p in result.packages]
    assert names == sorted(names)
    for dep in deps:
        name = dep.split("=")[0].split(">")[0].split("<")[0].strip()
        assert name in names
    for pkg in result.packages:
        assert pkg.sha256, f"{pkg.name} missing sha256"
        assert pkg.url, f"{pkg.name} missing url"


def test_solve_one_platform_unsatisfiable():
    result = solve_one_platform(
        channels=("conda-forge",),
        dependencies=["__nonexistent_package_xyz__"],
        platform="linux-64",
    )
    assert result.error is not None
    assert result.packages == []


@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(
            ["linux-64", "osx-arm64"],
            id="multi",
            marks=pytest.mark.crossplatform,
        ),
    ],
)
def test_solve(platforms):
    results = solve(["conda-forge"], ["zlib"], platforms)
    assert len(results) == len(platforms)
    for result, platform in zip(results, platforms):
        assert result.platform == platform
        assert result.error is None
        assert len(result.packages) > 0


@pytest.mark.parametrize(
    "fn_name",
    [
        pytest.param("solve", id="solve"),
        pytest.param("solve_environments", id="solve_environments"),
    ],
)
def test_defaults_to_current_platform(fn_name):
    fn = {"solve": solve, "solve_environments": solve_environments}[fn_name]
    results = fn(["conda-forge"], ["zlib"])
    assert len(results) == 1
    platform = (
        results[0].platform if hasattr(results[0], "platform") else results[0].platform
    )
    assert platform == context.subdir


@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(
            ["linux-64", "osx-arm64"],
            id="multi",
            marks=pytest.mark.crossplatform,
        ),
    ],
)
def test_solve_environments(platforms):
    envs = solve_environments(["conda-forge"], ["zlib"], platforms)
    assert len(envs) == len(platforms)
    for env, platform in zip(envs, platforms):
        assert isinstance(env, Environment)
        assert env.platform == platform
        names = [r.name for r in env.explicit_packages]
        assert "zlib" in names


@pytest.mark.usefixtures("_warm_index")
def test_build_index_returns_same_object():
    """Calling build_index twice returns the same cached instance."""
    idx1 = build_index(("conda-forge",), "linux-64")
    idx2 = build_index(("conda-forge",), "linux-64")
    assert idx1 is idx2


@pytest.mark.crossplatform
@pytest.mark.usefixtures("_warm_index")
def test_build_index_different_platforms():
    """Different platforms get separate cache entries."""
    idx_linux = build_index(("conda-forge",), "linux-64")
    configure_platform("osx-arm64")
    idx_osx = build_index(("conda-forge",), "osx-arm64")
    assert idx_linux is not idx_osx
    assert len(index_cache) >= 2


@pytest.mark.usefixtures("_warm_index")
def test_clear_index_cache():
    """clear_index_cache() forces a fresh index on next call."""
    idx1 = build_index(("conda-forge",), "linux-64")
    clear_index_cache()
    assert len(index_cache) == 0
    idx2 = build_index(("conda-forge",), "linux-64")
    assert idx2 is not idx1


def test_cached_solve_produces_correct_results():
    """Two back-to-back solves produce identical results (cache hit)."""
    clear_index_cache()
    r1 = solve(["conda-forge"], ["zlib"], ["linux-64"])
    r2 = solve(["conda-forge"], ["zlib"], ["linux-64"])
    names1 = [p.name for p in r1[0].packages]
    names2 = [p.name for p in r2[0].packages]
    assert names1 == names2


def test_solve_one_platform_generic_exception_sanitized(monkeypatch):
    """Generic exceptions return a generic message, not the raw str."""
    monkeypatch.setattr(
        "conda_presto.resolve.run_solver",
        lambda *a: (_ for _ in ()).throw(TypeError("/Users/secret/path")),
    )
    result = solve_one_platform(("conda-forge",), ["zlib"], "linux-64")
    assert result.error == "Internal solver error"
    assert "/Users/" not in (result.error or "")
    assert result.packages == []


def test_solve_one_platform_known_exception_surfaces_detail(monkeypatch):
    """Known solver errors (UnsatisfiableError/PackagesNotFoundError) surface detail."""

    def raise_pnf(*a, **kw):
        raise PackagesNotFoundError(["__nonexistent__"])

    monkeypatch.setattr("conda_presto.resolve.run_solver", raise_pnf)
    result = solve_one_platform(("conda-forge",), ["__nonexistent__"], "linux-64")
    assert result.error is not None
    assert "__nonexistent__" in result.error


def test_solve_result_error_sanitizes_generic():
    """solve_result_error returns generic message for unknown exception types."""
    exc = ValueError("/Users/secret/path")
    result = solve_result_error("linux-64", exc)
    assert isinstance(result, SolveResult)
    assert result.platform == "linux-64"
    assert result.error == "Internal solver error"
    assert result.packages == []


@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(["linux-64", "osx-arm64"], id="multi"),
    ],
)
def test_dispatch_on_error(platforms, monkeypatch):
    """dispatch catches errors when on_error is provided."""
    monkeypatch.setattr(
        "conda_presto.resolve.get_process_pool",
        lambda: ThreadPoolExecutor(max_workers=2),
    )

    def failing_fn(ch, deps, plat):
        raise RuntimeError(f"fail-{plat}")

    results = dispatch(
        failing_fn,
        ("conda-forge",),
        ["zlib"],
        platforms,
        on_error=lambda plat, exc: f"error:{plat}:{exc}",
    )
    assert len(results) == len(platforms)
    for result, plat in zip(results, platforms):
        assert f"error:{plat}:fail-{plat}" in result


@pytest.mark.parametrize(
    "platforms",
    [
        pytest.param(["linux-64"], id="single"),
        pytest.param(["linux-64", "osx-arm64"], id="multi"),
    ],
)
def test_dispatch_no_error_propagates(platforms, monkeypatch):
    """dispatch raises when on_error is None."""
    monkeypatch.setattr(
        "conda_presto.resolve.get_process_pool",
        lambda: ThreadPoolExecutor(max_workers=2),
    )

    def failing_fn(ch, deps, plat):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        dispatch(
            failing_fn,
            ("conda-forge",),
            ["zlib"],
            platforms,
        )


def test_run_solver_no_backend(monkeypatch):
    """run_solver raises RuntimeError if no solver backend is found."""
    monkeypatch.setattr(
        context.plugin_manager,
        "get_cached_solver_backend",
        lambda: None,
    )
    with pytest.raises(RuntimeError, match="No solver backend"):
        run_solver(("conda-forge",), ["zlib"], "linux-64")


def test_warmup_indexes():
    clear_index_cache()
    warmup_indexes(["conda-forge"], ["linux-64"])
    assert (("conda-forge",), "linux-64") in index_cache
    clear_index_cache()


@pytest.mark.parametrize(
    "platform, expected_keys",
    [
        pytest.param("linux-64", {"glibc", "linux"}, id="linux"),
        pytest.param("osx-arm64", {"osx"}, id="osx"),
        pytest.param("win-64", {"win"}, id="win"),
    ],
)
def test_configure_platform_virtual_package_overrides(
    platform, expected_keys, monkeypatch
):
    """Each platform family gets its own set of virtual package overrides."""
    monkeypatch.setattr(resolve_module, "current_platform", None)
    with platform_lock:
        configure_platform(platform)
    overrides = context._cache_.get("_override_virtual_packages", {})
    assert set(overrides.keys()) == expected_keys
    monkeypatch.setattr(resolve_module, "current_platform", None)


def test_shutdown_process_pool_is_idempotent():
    """shutdown_process_pool clears the global and is safe to call twice."""
    try:
        pool = get_process_pool()
        assert resolve_module.process_pool is pool
        shutdown_process_pool()
        assert resolve_module.process_pool is None
        shutdown_process_pool()
        assert resolve_module.process_pool is None
    finally:
        # Leave a fresh pool for any subsequent tests.
        get_process_pool()


def test_warmup_including_pool(monkeypatch):
    """warmup() calls warmup_indexes in both parent and worker processes."""
    calls = []
    monkeypatch.setattr(
        "conda_presto.resolve.warmup_indexes",
        lambda ch, plats: calls.append(("parent", ch, plats)),
    )

    class FakePool:
        def submit(self, fn, *args):
            f = Future()
            try:
                result = fn(*args)
                f.set_result(result)
            except Exception as exc:
                f.set_exception(exc)
            return f

    monkeypatch.setattr("conda_presto.resolve.get_process_pool", lambda: FakePool())
    warmup(["conda-forge"], ["linux-64", "osx-arm64"])
    assert calls[0] == ("parent", ["conda-forge"], ["linux-64", "osx-arm64"])
