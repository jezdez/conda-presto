# Caching and freshness

conda-presto caches channel metadata, solver indexes, public HTTP responses, and
private solver final states. These layers avoid different work and follow
different freshness rules. Scheduled refresh applies only to the private solver
cache.

## The cache layers

On-disk repodata
: Conda stores downloaded channel metadata in its normal package cache. Other
  conda processes can reuse those files. Conda's repodata settings determine
  when the files need to be refreshed.

Solver indexes
: A running solve process can retain a rattler index for a channel and platform
  combination. Reuse avoids rebuilding the index. A repodata refresh reloads
  the affected channel data.

Result cache
: The HTTP application keeps successful `/resolve` response bodies and private
  solver final states in one bounded in-process cache. A configured file or
  Redis store can preserve entries across restarts. The two entry types use
  separate key namespaces and retrieval contracts.

## Public resolve responses

A `/resolve` identity covers normalized specs, ordered channels, platforms,
output format and exporter provider, effective conda solve settings, effective
virtual-package records for the requested platforms, dependency versions, and
the local repodata file markers visible to the server.

Before reusing a stored result, a new GET or POST resolve request checks whether
the relevant repodata is stale. A stale snapshot bypasses the entry. A second
capture must also show unchanged markers before a cache hit is returned. After
a solve, conda-presto captures the markers again and publishes only when they
did not change during the operation.

Successful responses through the mutable cache path use
`Cache-Control: no-store`, even when the response is retained. This keeps
browsers and shared intermediaries from answering a new resolve request without
the application's freshness check. This is not a guarantee about every
validation or error response. Requests with detected credential patterns in a
channel or package spec are returned without retention or a content address.

The `/r/<hash>` resource has a different contract. It returns the stored body
for that address without another freshness check. An older permalink therefore
remains an immutable snapshot until eviction or persistent-store expiry, while
a later resolve request can produce a different location.

If metadata cannot be described safely, the response is returned without being
retained. Persistent-store failures also fall back to returning the foreground
response.

## Private solver final states

The Presto solver key describes the complete solve-affecting request sent by the
calling conda process. It includes installed records, requested changes, history,
pins, effective virtual packages, update and dependency modifiers, channel
order, target subdir, repodata mode, solver settings, and dependency versions.

The prefix path and file inventory are not sent. Local repodata TTL and current
repodata markers are not part of the key. Instead, each stored value records the
markers used by its solve. A lookup captures current markers under the caller's
TTL and returns the entry only when those markers are current and equal.

With conda's `use_index_cache` setting, an existing non-sharded JSON cache file
can remain eligible despite its normal TTL. Sharded metadata still participates
in the stale check. Marker equality remains required in both cases.

This key is intentionally specific. Repeated dry-runs, retries, and equivalent
prefix states can hit. A completed transaction usually changes installed
records or history, so the next operation often has a different key. Cache-hit
reuse skips state-specific index construction and SAT solving, but still pays
for broker communication, decoding, and repodata marker checks.

## Caller virtual packages

Direct CLI and public HTTP solves configure conda-presto's target overrides for
each requested Linux, macOS, or Windows platform. Conda's effective
virtual-package records for that platform can also include other plugin
detections or overrides, such as CUDA or architecture records. Public cache
identity captures the resulting records for every requested platform.

The internal solver's client serializes the calling conda process's effective
virtual-package records, including local overrides, and the service restores
those exact records. The broker host does not redetect virtual packages for the
request.

Different effective virtual packages select different solver cache entries.
This prevents two hosts or override configurations from sharing an incompatible
final state.

## Recorded requests

After a successful cacheable foreground `/solver/v1` response, the broker child
records the serialized request. Recording is separate from the final-state
entry. The recorded identity omits dependency versions and repodata markers so
the request can remain eligible after compatible upgrades or metadata refreshes.
It retains the local TTL because that setting controls the replay's freshness
policy.

A request becomes eligible after repeated successful use and expires after a
period without use. Count and recency keep frequently repeated requests within
the bounded catalog. Recording does not predict future demand and does not
change foreground cache identity.

Candidate persistence is best effort. It checkpoints after refresh cycles and
during graceful shutdown. A crash can lose observations made since the last
checkpoint. Requests whose serialized state contains detected credential
patterns remain memory-only. Catalog payload size, entry counts, counters, and
timestamps are validated before recorded requests become eligible for refresh.

## Scheduled refresh

The broker child periodically inspects eligible recorded requests. Current
entries need no solve. Missing or stale entries can be recomputed by a worker
created lazily for that cycle and stopped when the cycle ends.

Foreground work has priority. A cycle does not start another replay while a
foreground request is active or waiting. If foreground work arrives during a
replay, the current replay finishes or reaches its timeout, then the cycle stops
starting work.

Solver errors remove an unchanged candidate because replaying it again is not
useful until another successful foreground request records it. Timeouts and
infrastructure failures leave it eligible for a later cycle. Local file channels
are not retained because their metadata cannot be represented by the cache-file
marker contract.

There is no HTTP endpoint to list recorded requests, trigger refresh, or report
solver cache hits. Aggregate cycle counters are written to the broker service
log and remain process-local.

## Timeout trade-off

Solver work receives the configured deadline. Repodata and virtual-package
inspection runs in non-abandoned worker threads because that inspection uses
conda's process-global platform context. A request waits for an active
inspection to return instead of leaving it to mutate shared state after the
request ends. An inspection blocked in dependency, filesystem, or
operating-system code can therefore make observed latency exceed the configured
solve timeout. Request count, body, channel, platform, solver-state, and
concurrency limits still constrain admitted work. An untrusted deployment also
needs process or container resource limits.

## Effect of solver improvements

A faster rattler solver reduces misses, foreground recomputation, and scheduled
refresh time. A final-state cache hit already skips SAT solving, so its latency
changes little when the underlying solver becomes faster. As miss cost falls,
the relative advantage of a hit narrows while the consistency and restart
benefits of caching remain.

Exact key fields, limits, and failure handling are in {doc}`../reference/cache`.
Operational steps are in {doc}`../how-to/configure-result-cache` and
{doc}`../how-to/refresh-solver-cache`.
