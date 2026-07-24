# Cache reference

conda-presto uses one bounded in-process cache for public resolve responses and
private solver final states. A Litestar file or Redis store can provide a
second, persistent layer. The two entry types share capacity limits but use
separate key namespaces and retrieval rules.

## Cache surfaces

| Surface | Storage key | Stored value | Public retrieval |
|---|---|---|---|
| `GET` or `POST /resolve` | `resolve-v1:<sha256>` | Encoded response body and media type | `GET /r/<sha256>` while the entry remains available |
| Broker-only `POST /solver/v1` | `solver-v1:<sha256>` | Final-state response and repodata markers | None |

A retained `/resolve` response includes these headers:

```text
Location: /r/<sha256>
Cache-Control: no-store
```

Successful responses produced through the `/resolve` cache path use `no-store`
because a new request must reach the application's repodata freshness check.
Validation and other error responses do not all carry this header. A retained
`GET /r/<sha256>` response uses
`Cache-Control: public, max-age=86400, immutable`. The cache or persistent
expiry may remove that entry before an intermediary's HTTP freshness lifetime
ends. A missing or expired permalink returns HTTP 404. Private solver responses
use `Cache-Control: no-store`, never include `Location`, and cannot be fetched
through `/r/<sha256>`.

A request containing known credential patterns in a channel or package spec is
solved but not retained. The cache applies the same check to produced package
URLs because channel repodata can supply a separate package base URL. Detected
patterns include authentication, a token path, query parameters, and fragments.
The successful response uses `no-store` and has no `Location` header. Stored
entries that fail this check on read are removed instead of returned.

`GET /r/<sha256>` returns the stored body without repeating a repodata
freshness check. A later `/resolve` request can bypass stale metadata and move
to a new digest while an older permalink remains retrievable until its entry is
evicted or removed from the persistent store.

## In-process retention

`CONDA_PRESTO_RESULT_CACHE_SIZE` limits the combined number of resolve and
solver entries in the least-recently-used cache. Its default is 256. Set it to
`0` to disable in-process retention.

`CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` limits the combined encoded size of
those entries. Its default is 64 MiB. Set it to `0` to disable the byte limit.
An individual entry larger than a positive byte limit is not retained in
memory. It can still be written to a configured persistent store.

## Persistent stores

| Backend | Selection | Required setting |
|---|---|---|
| Memory | Default when no persistent setting is present | None |
| File | `CONDA_PRESTO_RESULT_CACHE_BACKEND=file`, or automatic when only a cache directory is set | `CONDA_PRESTO_RESULT_CACHE_DIR` |
| Redis | `CONDA_PRESTO_RESULT_CACHE_BACKEND=redis`, or automatic when a Redis URL is set | `CONDA_PRESTO_RESULT_CACHE_REDIS_URL`, otherwise `redis://localhost:6379/0` is used |

When the backend is unset and both persistent settings are present, the Redis
URL takes precedence over the file directory.

Redis keys use `CONDA_PRESTO_RESULT_CACHE_REDIS_NAMESPACE`, which defaults to
`conda-presto`. The published server image includes the Redis client. Other
Python installations need the `redis` optional dependency.

Persistent reads and writes are submitted in order. A cache caller waits up to
two seconds for an operation. A timeout or store failure is logged and treated
as a cache miss or a failed persistent write. It does not make an otherwise
valid solve response fail. Resolve and solver entries expire after 24 hours.
An encoded persistent value larger than 64 MiB is neither written nor loaded.
Corrupt, incompatible, or oversized values are removed when encountered.
File-backed stores attempt to remove expired values during application startup
and once per hour. Cleanup failures do not stop the service.

Expiry and per-value size checks do not bound the aggregate size of a
persistent store. Put a file store on a filesystem or volume with a hard quota.
Configure a dedicated Redis instance with `maxmemory` and a suitable eviction
policy such as `allkeys-lru`. A Redis namespace does not isolate the instance's
memory limit or eviction policy.

Persistent storage is a trusted service boundary. Anyone who can write its
files or Redis namespace can replace package results and warming inputs. Use a
dedicated service account, mode `0700` file directory, or authenticated Redis
instance. Do not share a namespace with less-trusted applications. The payload
format detects corruption but does not authenticate a writer that already has
store access.

## Resolve cache identity

The public digest is SHA-256 over a versioned canonical envelope containing:

- specs sorted by their string form
- channels in effective priority order
- requested platforms in order, or the native platform when omitted
- native JSON or the requested exporter selector
- for an exporter, its canonical provider name, selected callback module and
  qualified name, and versions of the installed distributions that provide
  the callback's import package
- installed versions of conda-presto, conda, conda-rattler-solver,
  and py-rattler
- effective channel priority, package-format selection, and canonical pinned
  package specs, plus dependency-cycle and implicit Python `pip` settings
- the effective virtual-package records captured for each requested platform,
  including configured target overrides and other detections or overrides
  exposed by conda's virtual-package plugins
- repodata records containing the public channel URL, selected metadata source,
  file size, modification and change times, device, and inode

Raw file content and filenames are not direct key fields. File requests are
parsed first, then the effective specs and channels enter the key. Spec order
does not change the digest. Channel and platform order do. Exporter responses
are not retained when conda-presto cannot identify the callback and its
providing distribution versions.

A lookup is skipped when the captured initial repodata snapshot is marked
stale. A cached value is returned only after another capture confirms that the
repodata markers did not change during the lookup. After a solve, conda-presto
captures the records again and publishes the response only when the markers
are unchanged, the resulting snapshot is fresh, and the metadata transition is
safe. Changed cache-file markers produce a different digest. A post-solve
snapshot with missing metadata files or local `file://` channels is not
retained. An ambiguous sharded-repodata fallback is also not retained.

## Solver cache identity

The private solver digest is SHA-256 over a versioned envelope containing the
operation name, serialized solve-affecting request state, and installed
versions of conda-presto, conda, conda-rattler-solver, and py-rattler.

The serialized state includes channels, subdirectories, requested changes,
installed records, history, pins, virtual packages, update settings, repodata
mode, and other rattler settings. It does not include the local prefix path or
prefix file inventory. `local_repodata_ttl` is omitted from the digest, but the
request restores it before capturing current repodata state. When
`use_index_cache` is true, conda-presto accepts an existing JSON repodata cache
regardless of that TTL. Sharded repodata still uses conda's stale check.

Repodata markers are stored with the final state instead of being included in
the digest. A hit requires the current snapshot to be fresh and its records to
equal those stored with the response. Publication requires the worker's
before-solve and used markers to match, followed by another current snapshot
that still matches. Solver errors and results without that unchanged, safe
metadata transition are not retained. A current result replaces an older entry
under the same private key after repodata changes.

## Timeout boundary

The configured solve timeout supplies the deadline for solver work. Cache-state
inspection runs in non-abandoned worker threads because it temporarily changes
conda's process-global platform context. The request waits for that inspection
to finish rather than leaving it active after returning a timeout. If metadata
inspection blocks inside conda, filesystem, or operating-system code, the
observed request duration can therefore exceed the configured timeout. Channel,
platform, body, state, and concurrency limits still bound admitted work. Use
process or container resource limits when serving untrusted callers.

## Recorded solver requests

A successful foreground solver request is recorded when its result was a cache
hit, was newly published, or matched an already-current entry. Recording is
disabled when `CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_SIZE=0`.

The recorded-request identity contains the complete serialized request,
including its local repodata TTL, but omits dependency versions. The default
catalog limit is 32. Up to the same number of observations can be retained
outside the catalog. Catalog order uses request count, then most recent request
time, then fingerprint. A request is eligible for scheduled refresh after two
recorded uses and expires seven days after its most recent use.

Recorded requests are process-local by default. Set
`CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_PERSIST=true` to checkpoint them in a
configured file or Redis store. This setting is rejected with the memory-only
backend. Requests whose serialized state contains detected credential patterns
or tokenized URLs remain memory-only. Persistent catalogs larger than 8 MiB,
invalid counters, non-finite or future timestamps, and corrupt payloads are
rejected. Persistent catalogs are loaded at startup and checkpointed after
refresh cycles and during graceful shutdown. Persistence is best effort. A
process crash can lose requests recorded since the most recent checkpoint.

## Scheduled solver-cache refresh

Scheduled refresh is enabled only when all of these conditions hold:

- `CONDA_BROKER_SERVICE_NAME` is `conda-presto.server`
- persistent-worker mode is enabled
- the refresh interval, batch size, and recorded-request limit are positive
- the in-process result cache has positive capacity or a persistent store is
  configured

The first cycle starts after 30 seconds. Later cycles use
`CONDA_PRESTO_SOLVER_CACHE_WARM_INTERVAL_S`, which defaults to 300 seconds. A
cycle considers at most `CONDA_PRESTO_SOLVER_CACHE_WARM_BATCH_SIZE` requests,
which defaults to 8. With a memory-only result cache, the batch is also capped
by its entry limit.

Each metadata inspection, refresh-worker startup, or solve receives a
30-second deadline and respects a lower `CONDA_PRESTO_SOLVE_TIMEOUT_S`. A
non-abandoned metadata inspection can delay completion beyond that deadline.
A cycle stops starting work after 60 seconds. No replay starts while foreground
solver work is active or waiting.
If foreground work arrives during a replay, that replay finishes or times out
and the cycle starts no further request.

The refresh worker is separate from the foreground worker. It starts lazily,
is reused within one cycle, and shuts down at the end of that cycle. Docker and
ordinary HTTP servers do not satisfy the broker identity requirement and do
not run scheduled refresh.

| Cycle outcome | Recorded-request handling |
|---|---|
| Current cache entry | Keep the request and skip solving |
| Missing or stale entry with a cacheable solve | Publish the current final state and keep the request |
| Local `file://` source | Discard the request |
| Solver error | Discard the request unless a concurrent foreground use updated it |
| Metadata failure, timeout, infrastructure failure, or rejected publication | Keep the request for a later cycle |
| Required persistent write failure | Keep the request and count a rejected publication |

## See also

- {doc}`/reference/environment-variables`
- {doc}`/reference/solver-backend`
- {doc}`/reference/observability`
- {doc}`/explanation/performance`
