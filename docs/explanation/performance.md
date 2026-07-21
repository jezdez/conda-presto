# Performance

conda-presto is intended for solve-only work where creating a prefix would be
unnecessary. Latency depends more on process state, repodata freshness, and
cache identity than on the number of requested package names alone.

## Cost model

A direct solve can pay for:

1. Python startup and conda imports.
2. Input parsing and normalization.
3. Repodata download or freshness checks.
4. Solver index construction or refresh.
5. SAT solving.
6. Result conversion, export, and serialization.

Network state and repodata dominate many cold runs. Once metadata and indexes
are available, SAT solving and process overhead become more visible.

## Execution mode changes the baseline

One-shot CLI
: Pays process startup and imports on every command. Conda's on-disk repodata
  cache can survive between commands.

Ordinary HTTP server
: Keeps the web application and result cache alive. Startup primes conda's
  on-disk repodata cache. Each uncached request runs in an isolated child
  process, so an in-memory rattler index is not reused by later requests.

Docker server
: Adds a persistent foreground worker that retains loaded solver state and
  indexes. Requests are serialized through that worker.

Broker-managed service
: Uses the same persistent-worker model and can also serve the internal Presto
  solver. Scheduled refresh runs only in this mode.

Comparisons must name the mode. A cold CLI invocation and a warm broker request
measure different work even when their package specs match.

## Metadata and index reuse

Conda's on-disk repodata cache removes repeat downloads while the metadata is
acceptable under the effective policy. Ordinary HTTP startup fetches or
validates the configured repodata in that disk cache, but it does not leave a
reusable rattler index in the per-request solve processes. Only a persistent
worker retains a rattler index for the same channel and platform combination
across requests.

When metadata becomes stale or its cache-file markers change, conda-presto
reloads affected index data before solving. Persistent-worker startup shifts
both initial repodata and index work into service readiness. Ordinary HTTP
startup shifts only repodata work. Neither mode precomputes arbitrary solve
results.

## Full result cache hits

A retained `/resolve` response can skip solving and serialization work after a
freshness-qualified key lookup. The key includes current repodata markers, so a
metadata refresh can lead to a new content address.

A private solver final-state hit skips state-specific index construction and SAT
solving. It still pays for the loopback request, decoding, and repodata marker
checks. The key includes installed records, history, pins, virtual packages,
requested changes, and solver settings. A repeated dry-run with unchanged
prefix state can hit. A completed transaction usually changes the next key.

This makes solver-cache reuse workload-dependent. Repeated CI-like dry-runs,
retries, and cloned prefix states have more reuse than a sequence of distinct
updates to one changing prefix.

There is currently no response header or CLI field that identifies a private
solver cache hit. Do not infer a hit from one timing sample.

## Scheduled refresh

Scheduled refresh moves selected cache-miss work away from a later foreground
request. It does not make every recorded request current at all times.

The broker considers only requests that were used repeatedly and still fit its
bounded catalog. It checks a limited batch per cycle and gives foreground work
priority. A replay worker exists only while a cycle needs it. This bounds idle
resource use but means a large catalog can take several cycles to inspect.

Refresh consumes CPU, memory, repodata I/O, and persistent-store capacity. The
interval and batch size should reflect how often repodata changes, how expensive
misses are, and how much idle capacity the host has.

## Solver improvements and cached workloads

A faster rattler implementation improves cache misses and background refreshes.
It also narrows the relative latency difference between a miss and a full
final-state hit. Cache hits still avoid solver work entirely, so they continue
to benefit workloads with stable request identity.

Classic conda, libmamba, rattler, and the Presto service cannot be compared from
one number. A useful comparison controls channels, repodata state, target
platform, prefix state, operation modifiers, process warmth, and whether the
Presto result cache is a hit or a miss.

## Multi-platform solving

Direct multi-platform requests dispatch one solve per platform. With enough
workers, wall time approaches the slowest platform more closely than the sum of
all platform times. Each worker has its own Python process and index memory, so
increasing `CONDA_PRESTO_WORKERS` trades memory for parallelism.

The Docker and broker deployments accept one foreground request at a time, but
a multi-platform request can still use the worker's internal process pool.

## Lockfile conversion

Covered lockfile conversion is usually the smallest work path. It parses
existing records and renders another lockfile format without channel access or
SAT solving. If requested platforms are missing or the request adds specs or
channel overrides, conda-presto refuses transcode rather than silently solving.

## Measure your workload

There is no single representative conda solve. Measurements are meaningful
only when they distinguish process startup, repodata state, retained indexes,
and full result-cache hits. The repository does not commit historical result
snapshots or define a service-level performance guarantee.

Use {doc}`../how-to/benchmark-performance` to run the repository benchmarks and
compare controlled HTTP profiles. Record enough context for another person to
reproduce the same cache and process state.

The cache contracts behind these effects are described in
{doc}`caching-and-freshness` and {doc}`../reference/cache`.
