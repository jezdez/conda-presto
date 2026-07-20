# Presto solver backend

`presto` is an internal `conda_solvers` plugin. Select it with
`conda --solver=presto ...` after installing conda-presto and starting the
broker-managed `conda-presto.server` service.

## Lifecycle and discovery

The backend calls `Broker.current().service("conda-presto.server")` and requires
its default endpoint to be ready. It accepts only an HTTP endpoint whose URL host
is `localhost` or an IP address classified as loopback, including the full
`127.0.0.0/8` range and `::1`. The handler also accepts only loopback clients.
It never starts, stops, or configures the broker service.
The client disables HTTP redirects and environment proxy handling so the
serialized solve state is sent directly to that loopback endpoint only.

When no ready service is available, the backend reports an error directing the
caller to:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server
```

There is no `CONDA_PRESTO_SOLVER_URL` setting or remote endpoint mode.

## Solve semantics

Only the conda `Solver.solve_final_state()` operation is delegated. The client
serializes installed package records, history, pins, virtual packages,
requested specs, channel definitions, and the effective channel-priority,
package-format, implicit Python `pip` dependency, dependency-cycle,
free-channel, repodata-shard, index-cache, and local repodata TTL settings. The
effective repodata filename selected by the client remains authoritative in the
service. The service reconstructs that state with private
`conda-rattler-solver` APIs and forces its `rattler` backend. Packages-not-found,
unsatisfiable, and pin conflict errors are
reconstructed in the client as their conda exception categories so conda's
retry and error handling still applies.

The client still uses conda's `solve_for_diff()` and transaction code.
`--force-reinstall` is therefore handled by conda's local unlink/link selection.

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

The solver cache key hashes the solve-affecting serialized request fields, the
effective repodata filename, the operation identifier, and the conda-presto,
conda, conda-rattler-solver, and py-rattler versions. Prefix paths, file
inventories, and the local repodata TTL are not included. The TTL is applied to
every freshness check, so callers with different TTLs can share an entry only
while its repodata markers are fresh for the current caller. Changed installed
records, history, pins, virtual packages, requested specs, settings, channels,
or dependency versions select a different entry.

Each entry stores the successful response and the repodata cache-file markers
recorded by the worker after index collection: channel URL, selected JSON or
sharded source, file size, modification time, and freshness state. A hit
requires conda to consider the current cache files fresh and their markers to
match the stored markers. A result is not retained when the markers before or
after index collection are unavailable, the current markers differ, or a
transient JSON fallback leaves an old shard marker unchanged. After a repodata
refresh, a retained result replaces the entry under the same request key.
Missing repodata and local `file://` sources remain uncacheable. Errors and
metadata-free early exits are not retained.

In-memory solver entries share `CONDA_PRESTO_RESULT_CACHE_SIZE` and
`CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` with `/resolve`. They can use the
configured persistent result store, whose retention is managed separately, but
they have no public `/r/<hash>` permalink or public immutable cache headers.
Measure hit rates against the commands and prefix states used by the deployment.
Treat a configured file or Redis store as private because final states can
contain package metadata from credentialed channels.

## Internal protocol

The broker child enables `POST /solver/v1` through its
`CONDA_BROKER_SERVICE_NAME` identity. It must identify the
`conda-presto.server` broker child. The handler is absent from the public OpenAPI
contract and requires the broker's persistent worker. The Docker server does not
enable it. Request and response logging excludes this route so channel
credentials and installed-prefix state are not written to broker logs.

`/solver/v1` is a private implementation detail, not an HTTP API to integrate
against. Its message format and behavior may change or be removed without a
semver compatibility promise. The feature is tied to conda-presto's supported
`conda` and `conda-rattler-solver` version ranges and should use matching client
and service environments.

Conda does not expose structured public accessors for
`SpecsConfigurationConflictError`, so this private protocol reads its private
`_kwargs` payload to reconstruct the same exception in the client.
