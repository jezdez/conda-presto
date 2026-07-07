# Performance

conda-presto is optimized for repeated dry-run solves: CI jobs, server-backed
tools, and workflows that need lockfiles without creating environments. The
main performance question is whether the request can reuse warmed metadata,
indexes, or a full cached response.

## Where time goes

A solve has four broad costs:

1. Parse input into conda specs and channels.
2. Load or refresh channel repodata.
3. Build or reuse the solver index for each channel/platform pair.
4. Run SAT solving and serialize the response.

For small environments, Python startup and conda imports can dominate CLI
latency. In server mode those costs are paid once at process startup, so warm
requests spend most of their time in the solver and exporter.

## Cache layers

On-disk repodata cache
: conda downloads channel metadata into its normal local cache. This cache is
  shared with other conda commands and is controlled by conda's repodata TTL
  settings.

In-memory solver index cache
: each process keeps a `RattlerIndexHelper` per `(channels, platform)` key.
  Once built, the index stays warm for the lifetime of that process.

Content-addressed result cache
: successful HTTP `/resolve` responses are stored by a SHA-256 key. A cache hit
  skips solving and exporting entirely and returns the stored body. The in-memory
  LRU can be backed by a persistent file or Redis store, which lets cached
  results survive server restarts.

## Cache key safety

The result cache key is tied to the inputs that can change the response:
normalized specs, ordered channels, target platforms, output format, relevant
dependency versions, and markers for conda's local `repodata.json` and state
files. When conda refreshes repodata, those file markers change and the next
request gets a different key.

That design keeps shared caching practical for public channels while avoiding
reuse across channel metadata snapshots. Private channels and credentialed
channel URLs should use an isolated deployment until the cache model grows an
explicit private-channel policy.

## First solve vs. warm solve

The first request for a new channel/platform combination pays the repodata and
index-build costs. Later requests in the same process reuse the in-memory
index. Server startup can pre-warm expected channel/platform combinations using
`CONDA_PRESTO_CHANNELS` and `CONDA_PRESTO_PLATFORMS`, shifting that cost from
the first user request to startup.

The result cache adds one lightweight store lookup on each HTTP solve request.
On a miss, conda-presto computes the key before solving and recomputes it after
the solve so a cold repodata cache stores the result under the post-refresh
metadata markers. That overhead scales with the number of channel/platform
repodata files and is normally much smaller than solving.

## Multi-platform solving

Multi-platform requests run one solve per platform through a persistent process
pool. Wall-clock time follows the slowest platform solve more closely than the
sum of all platform solves, assuming enough workers are available. Tune the
pool with `CONDA_PRESTO_WORKERS`; tune concurrent HTTP requests with
`CONDA_PRESTO_CONCURRENCY`.

## Lockfile transcoding

Lockfile-to-lockfile conversion is the fastest path because it does not invoke
the solver. When the requested platforms already exist in the input lockfile,
conda-presto reuses those package records and renders them through the target
lockfile exporter.

## Benchmarking

The repository keeps benchmark inputs and historical result snapshots under
`benchmarks/`. Rerun them with:

```bash
pixi run bench
```

Treat benchmark snapshots as local measurements, not service-level guarantees.
They depend on machine size, network state, conda's repodata cache, selected
channels, and whether the process has already built solver indexes.
