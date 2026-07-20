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
  Reuse follows conda's repodata freshness policy. When repodata expires or a
  cache file changes, the existing index reloads those channels before solving.

Result cache
: successful HTTP `/resolve` responses and internal `/solver/v1` final states
  share a bounded in-process LRU. Resolve responses use content-addressed keys.
  Solver final states use keys derived from serialized request fields and
  dependency versions. Each solver entry also records repodata cache-file
  markers. A solver hit skips state-specific index construction and SAT solving,
  but the cached response is still encoded by the service and decoded by the
  client. The LRU can be backed by a persistent file or Redis store, which lets
  cached results survive server restarts.

Cache-warming candidates
: successful cacheable foreground solver requests are recorded locally. A
  request becomes eligible for refresh after repeated use. Candidates are
  ordered by request count and recency; background refreshes do not change
  either value. A bounded observation tier retains requests outside the catalog,
  including displaced candidates. An observation and the lowest-ranked candidate
  exchange places when the observation's count, then recency, ranks higher.

## Cache keys and repodata checks

The result cache key is tied to the inputs that can change the response:
normalized specs, ordered channels, target platforms, output format, relevant
dependency versions, and markers for conda's local `repodata.json` files. A
request bypasses a stored result when conda's effective policy requires any
corresponding repodata metadata to refresh. If refreshed package metadata
changes, the next `/resolve` result uses a different key. Solver final-state
keys hash solve-affecting request fields, including installed records, history,
pins, virtual packages, operation modifiers, solver settings, and channel
definitions, along with dependency versions. They exclude the prefix path, file
inventory, local repodata TTL, and repodata markers, allowing the same
solve-affecting request fields at different paths or TTLs to use one entry. The
stored value includes the URL, selected source, file size, modification time,
and freshness state recorded for each repodata cache file. A lookup returns the
entry only when conda considers the current files fresh under the caller's TTL
and their markers match.

Private channels and credentialed channel URLs should use an isolated
deployment until the cache model has an explicit private-channel policy.

## First solve vs. warm solve

The first request for a new channel/platform combination pays the repodata and
index-build costs. Later requests in the same process reuse the in-memory
index. Server startup can pre-warm expected channel/platform combinations using
`CONDA_PRESTO_CHANNELS` and `CONDA_PRESTO_PLATFORMS`, shifting that cost from
the first user request to startup.

The result cache adds a store lookup and freshness check on each HTTP solve
request. On a solver miss, conda-presto captures metadata immediately before
and after worker index collection, then compares the current cache-file markers
before replacing the existing solver entry. The number of checks scales with
the number of channel/platform repodata files.

The final-state cache has narrower reuse than `/resolve`: repeated dry-runs,
retries, creates, and cloned prefix states can hit, while a completed transaction
normally changes the installed records and therefore the next key. Requests
with different prefix histories or pins also use different keys.

Candidate identity omits dependency versions and current repodata markers so a
recorded request can remain eligible after metadata refreshes and compatible
upgrades. Cache reuse still includes dependency versions and requires the
current repodata markers to match. Candidates are local by default; persistence
excludes requests with detected credentials.

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
