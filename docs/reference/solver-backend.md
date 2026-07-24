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
repodata-shard, index-cache, and local repodata TTL settings. The effective
repodata filename selected by the client remains authoritative in the service.
The service reconstructs that state with private
`conda-rattler-solver` APIs and forces its `rattler` backend. Packages-not-found,
unsatisfiable, and pin conflict errors are
reconstructed in the client as their conda exception categories so conda's
retry and error handling still applies.

The client still uses conda's `solve_for_diff()` and transaction code.
`--force-reinstall` is therefore handled by conda's local unlink/link selection.

The backend rejects operations it cannot reproduce accurately:

- offline solves, because the service does not receive the client's package
  cache paths.
- `--update-deps`, because rattler implements it with a recursive solver that
  would otherwise read the service's empty synthetic prefix.
- conda-build caller-provided indexes or repodata-subset callbacks.

Create, install, update with the default update strategy, remove, and
force-remove are supported. Unsupported state raises a conda error before a
request is sent. Offline mode and `--update-deps` are also rejected at the
service boundary.

## Performance, cache, and refresh

The broker keeps Python, conda, and rattler loaded and benefits from conda's
on-disk repodata cache. Before constructing a state-specific rattler index, the
handler checks the private `solver-v1:` result namespace. A current hit skips
index construction and SAT solving.

The cache identity covers the serialized solve-affecting state and dependency
versions. Prefix paths, prefix file inventories, and the local repodata TTL are
excluded. Stored repodata markers must still be fresh and match before a hit is
used. Solver entries share the result cache's in-process limits and optional
persistent store, but are never exposed through `/r/<hash>` or public cache
headers. See {doc}`cache` for the exact identity, retention, freshness, and
privacy rules.

Successful cacheable foreground requests can enter the recorded-request
catalog. In the broker service, scheduled refresh replays eligible missing or
stale entries without starting new work while foreground solver work is active
or waiting. See {doc}`cache` for selection, persistence, timing, failure, and
worker-lifecycle behavior. Freshness and foreground trade-offs are covered in
{doc}`../explanation/performance`.

## Internal protocol

The server process enables `POST /solver/v1` when its
`CONDA_BROKER_SERVICE_NAME` is `conda-presto.server`. That identity is process
state, not a request credential. The handler also requires a loopback peer, the
broker-assigned HTTP authority, a JSON request without a browser `Origin`, and
the broker's persistent worker. These checks prevent browser and DNS-rebinding
requests, but they do not authenticate operating-system users. A local process
that can discover the endpoint remains inside the broker service's trust
boundary. The Docker server does not enable the route or run scheduled
solver-cache refresh.

Request and response access logging excludes this route. Unexpected service
errors use a generic solver error. See {doc}`observability` for log filtering
and error-redaction behavior.

The handler accepts at most
`CONDA_PRESTO_MAX_SOLVER_CHANNELS` serialized channels. It also applies
`CONDA_PRESTO_MAX_SOLVER_STATE_ITEMS` to the combined number of requested
additions, removals, installed records, history entries, pins, virtual packages,
aggressive updates, and always-update entries. The defaults are 128 channels
and 10,000 state items. The ordinary public spec and channel limits do not
replace these protocol-specific bounds.

The client accepts at most 8 MiB in the solver response body. A larger response
closes the loopback connection and raises `PrestoSolverError` without decoding
the body.

`/solver/v1` is a private implementation detail, not an HTTP API to integrate
against. Its message format and behavior may change or be removed without a
semver compatibility promise. The feature is tied to conda-presto's supported
`conda` and `conda-rattler-solver` version ranges and should use matching client
and service environments.

Conda does not expose structured public accessors for
`SpecsConfigurationConflictError`, so this private protocol reads its private
`_kwargs` payload to reconstruct the same exception in the client.

`conda-rattler-solver` 0.1.1 also has no constructor input for captured virtual
packages. The service currently replaces that one private input-state mapping
after construction. The public injection point is tracked by
[conda-rattler-solver #105](https://github.com/conda/conda-rattler-solver/issues/105)
and [PR #98](https://github.com/conda/conda-rattler-solver/pull/98). conda-presto
can remove this override after a release containing that API becomes its
minimum supported version.
