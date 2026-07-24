"""Benchmarks for conda_presto hot paths using pytest-benchmark.

Compares the serialized (``ResolvedPackage``/``SolveResult`` ->
``msgspec.json``) path against the exporter-plugin path
(``PackageRecord``/``Environment``) to verify the msgspec path
provides measurable serialization and memory benefits.
"""

from __future__ import annotations

import sys

import msgspec
import pytest
from conda.models.environment import Environment

from conda_presto.resolve import (
    ResolvedPackage,
    SolveResult,
    solve,
)


@pytest.fixture()
def resolved_packages(many_records):
    return [ResolvedPackage.from_record(r) for r in many_records]


@pytest.fixture()
def solve_result(resolved_packages):
    return SolveResult(platform="linux-64", packages=resolved_packages)


def test_bench_from_record(benchmark, sample_record):
    benchmark(ResolvedPackage.from_record, sample_record)


def test_bench_from_record_batch(benchmark, many_records):
    benchmark(lambda: [ResolvedPackage.from_record(r) for r in many_records])


def test_bench_msgspec_encode_single(benchmark, sample_resolved_package):
    benchmark(msgspec.json.encode, sample_resolved_package)


def test_bench_msgspec_encode_batch(benchmark, resolved_packages):
    benchmark(msgspec.json.encode, resolved_packages)


def test_bench_msgspec_encode_solve_result(benchmark, solve_result):
    benchmark(msgspec.json.encode, solve_result)


@pytest.mark.parametrize(
    "deps",
    [
        pytest.param(["zlib"], id="1-dep"),
        pytest.param(["python=3.12", "numpy"], id="2-deps"),
    ],
)
def test_bench_solve(benchmark, deps):
    benchmark(solve, ["conda-forge"], deps, ["linux-64"])


@pytest.mark.crossplatform
def test_bench_solve_multi_platform(benchmark):
    benchmark(solve, ["conda-forge"], ["zlib"], ["linux-64", "osx-arm64"])


def test_bench_server_path_serialize(benchmark, many_records):
    """Server path: PackageRecord -> ResolvedPackage -> msgspec.json."""

    def run():
        packages = [ResolvedPackage.from_record(r) for r in many_records]
        result = SolveResult(platform="linux-64", packages=packages)
        return msgspec.json.encode(result)

    benchmark(run)


def test_bench_cli_path_construct(benchmark, many_records):
    """CLI path: PackageRecord stays as-is in Environment."""
    benchmark(
        lambda: Environment(platform="linux-64", explicit_packages=list(many_records))
    )


def test_bench_memory_shallow_sizes(many_records, resolved_packages, solve_result):
    """Report shallow sizes (sys.getsizeof) for both paths.

    sys.getsizeof is unreliable for auxlib Entity objects —
    it reports only the container size, not internal storage.
    This test is informational only.
    """
    env = Environment(platform="linux-64", explicit_packages=list(many_records))

    server_size = sys.getsizeof(solve_result) + sum(
        sys.getsizeof(p) for p in resolved_packages
    )
    cli_size = sys.getsizeof(env) + sum(sys.getsizeof(r) for r in many_records)

    print(f"\n  Server (SolveResult+ResolvedPackage): {server_size:,} bytes")
    print(f"  CLI (Environment+PackageRecord):      {cli_size:,} bytes")
    print("  Note: sys.getsizeof underreports auxlib Entity sizes")
