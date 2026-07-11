# Presto solver backend

`presto` is an internal `conda_solvers` plugin. Select it with
`conda --solver=presto ...` after installing conda-presto and starting the
broker-managed `conda-presto.server` service.

## Lifecycle and discovery

The backend calls `Broker.current().service("conda-presto.server")` and requires
its default endpoint to be ready. It accepts only an HTTP endpoint whose URL host
is `localhost`, `127.0.0.1`, or `::1`, and the handler accepts only loopback
clients. It never starts, stops, or configures the broker service.
The client disables HTTP redirects and environment proxy handling so the
serialized solve state is sent directly to that loopback endpoint only.

When no ready service is available, the backend reports an error directing the
caller to:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server
```

There is intentionally no `CONDA_PRESTO_SOLVER_URL` setting and no remote
endpoint mode.

## Solve semantics

Only the conda `Solver.solve_final_state()` operation is delegated. The client
captures minimal package-record data for the installed prefix, history, pins,
virtual packages, requested specs, channel definitions, and the effective
channel-priority, package-format, implicit Python `pip` dependency,
dependency-cycle, free-channel, and repodata-shard settings. The index-cache
setting is captured as well. The service reconstructs that state
with private `conda-rattler-solver` APIs and forces its `rattler` backend. Known
packages-not-found, unsatisfiable, and pin conflict errors are reconstructed in
the client as their normal conda exception categories so conda's retry and
error handling still applies.

The client still uses conda's normal `solve_for_diff()` and transaction code.
Consequently, `--force-reinstall` affects local unlink/link selection exactly as
it does for other backends.

The backend rejects operations it cannot reproduce accurately:

- offline solves, because the service does not receive the client's package
  cache paths;
- `--update-deps`, because rattler implements it with a recursive solver that
  would otherwise read the service's empty synthetic prefix; and
- conda-build caller-provided indexes or repodata-subset callbacks.

Create, install, update with the default update strategy, remove, and
force-remove are supported. Unsupported state raises a conda error before a
request is sent. Offline mode and `--update-deps` are also rejected at the
service boundary.

## Performance and caching

The broker keeps Python, conda, and rattler loaded and benefits from conda's
on-disk repodata cache. Before constructing a state-specific rattler index, the
`/solver/v1` handler checks the bounded in-process cache used by `/resolve` and
its optional persistent file or Redis store, under a private `solver-v1:`
namespace. A hit skips index construction and SAT solving.

The stable slot key contains the complete canonical solver-relevant state,
effective ordered channels and credential scope, target subdirs, solver
settings, and conda-presto/conda/rattler versions. Prefix paths and file
inventories are not included, so identical logical prefix states can share a
result. Changed installed records, history, pins, virtual packages, requested
specs, settings, or dependency versions select a different slot.

Each slot stores the successful response with the exact JSON or
sharded-repodata snapshot observed by the worker after index collection. A hit
requires a fresh current snapshot with the same records. A solve is published
only when the worker's pre-index, used, and current snapshots prove which
metadata produced it; a metadata change during the solve, an unavailable
snapshot, or a transient JSON fallback that leaves an old shard marker
unchanged returns the result without retaining it. Refreshing repodata
atomically overwrites the same stable slot instead of creating one persistent
entry per metadata generation. Missing repodata and local `file://` sources
remain uncacheable. Errors and metadata-free early exits are not retained.

In-memory solver entries share `CONDA_PRESTO_RESULT_CACHE_SIZE` and
`CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` with `/resolve`. They can use the
configured persistent result store, whose retention is managed separately, but
they have no public `/r/<hash>` permalink or public immutable cache headers.
Measure hit rates against the commands and prefix states used by the deployment.
Treat a configured file or Redis store as private because final states can
contain package metadata from credentialed channels.

## Internal protocol

The broker child enables `POST /solver/v1` through
`CONDA_PRESTO_SOLVER_ENDPOINT=1`. The handler is absent from the public
OpenAPI contract and requires the broker's persistent worker. The Docker server
does not enable it. Request and response logging excludes this route so channel
credentials and installed-prefix state are not written to broker logs.

`/solver/v1` is a private implementation detail, not an HTTP API to integrate
against. Its message format and behavior may change or be removed without a
semver compatibility promise. The feature is tied to conda-presto's supported
`conda` and `conda-rattler-solver` version ranges and should use matching client
and service environments.

Conda does not expose structured public accessors for
`SpecsConfigurationConflictError`, so this private protocol reads its private
`_kwargs` payload to reconstruct the same exception in the client.
