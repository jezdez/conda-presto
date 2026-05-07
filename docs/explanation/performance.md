# Performance

conda-presto is designed to be fast enough for interactive use and CI pipelines.
This page explains the caching strategy, why first solves are slower than
subsequent ones, and includes benchmark numbers from real workloads.

## Index caching

Solving a conda environment requires a searchable index built from channel
repodata. Building that index is the most expensive part of a solve.

conda-presto uses two cache layers:

On-disk repodata cache
: conda's standard repodata cache with TTL-based expiration. Repodata is
  fetched from channels and stored locally. This cache is shared across all
  conda tools, so a recent `conda install` or `conda update` warms it for
  conda-presto too.

In-memory index cache
: A `RattlerIndexHelper` instance is built from the on-disk repodata and cached
  in memory, keyed by `(channels, platform)`. Once built, the index stays in
  memory for the lifetime of the process (or server).

## First solve vs. subsequent solves

The first solve for a given channel/platform pair pays roughly 700 ms to parse
repodata and build the in-memory index, plus the SAT solving time itself.
Subsequent solves with the same channels and platform skip the index build
entirely and pay only SAT time.

In server mode, this means the first request after startup (or after a new
channel/platform combination is seen) is noticeably slower. All following
requests for the same combination are fast.

## Multi-platform parallel solving

When resolving for multiple platforms at once (for example, `linux-64`,
`osx-arm64`, and `win-64`), conda-presto runs each platform solve in a
separate process via `ProcessPoolExecutor`. Wall-clock time scales with the
slowest single-platform solve rather than the sum of all platforms.

## Server pre-warming

The HTTP server can pre-warm the index cache on startup by solving a minimal
spec for each configured channel/platform pair. This moves the index build
cost to startup time so the first real request does not pay the penalty.

## Benchmarks

### CLI (hyperfine, macOS ARM64, warm cache, conda-forge)

```{list-table}
:header-rows: 1
:widths: 50 15 15 15

* - Scenario
  - Mean
  - Min
  - Max
* - zlib, 1 platform
  - 0.79 s
  - 0.77 s
  - 0.86 s
* - zlib, 3 platforms
  - 1.34 s
  - 1.33 s
  - 1.37 s
* - py+scipy+pandas+matplotlib, 1 platform
  - 0.94 s
  - 0.92 s
  - 1.00 s
* - py+scipy+pandas+matplotlib, 3 platforms
  - 1.87 s
  - 1.80 s
  - 1.94 s
* - py+pytorch+transformers+sklearn (11 pkgs), 1 platform
  - 1.90 s
  - 1.88 s
  - 1.93 s
* - py+pytorch+transformers+sklearn (11 pkgs), 3 platforms
  - 4.44 s
  - 4.37 s
  - 4.52 s
```

```{note}
CLI times include Python startup (~50 ms), pixi overhead (~50 ms), and
conda import (~200 ms). The solver itself is faster than these numbers
suggest.
```

### In-process server (pytest-benchmark, warm index)

```{list-table}
:header-rows: 1
:widths: 60 30

* - Operation
  - Time
* - Single-platform solve (zlib)
  - ~15 ms
* - Single-platform solve (python=3.12, numpy)
  - ~95 ms
* - ResolvedPackage.from_record (single)
  - ~2.1 us
* - ResolvedPackage.from_record (100 records)
  - ~222 us
* - msgspec.json.encode(ResolvedPackage)
  - ~242 ns
* - msgspec.json.encode(SolveResult) (100 packages)
  - ~13 us
* - Server path: records -> SolveResult -> JSON (100 pkgs)
  - ~234 us
```

These in-process numbers show the cost of the solve and serialization steps
without any CLI or HTTP overhead. The gap between "15 ms for zlib" and
"0.79 s CLI" is almost entirely Python and conda startup cost.
