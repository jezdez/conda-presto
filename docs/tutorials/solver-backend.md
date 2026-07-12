# Use the Presto solver backend

`conda --solver=presto` is an internal backend. It sends serialized fields from
the local solve request to conda-presto's persistent, loopback-only broker
service, then lets conda create the transaction locally.

It is useful for testing broker-backed create, install, update, and remove
solves when the service is already part of the local workflow. It is not a
remote solver interface and is not enabled in the Docker image.

## Start the local service

The solver never starts a service itself. Start it explicitly and wait for its
worker before selecting the backend:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server
```

## Select the backend for one command

Pass `--solver=presto` to a normal conda command. `--dry-run` is a good first
use for this internal backend:

```bash
conda create --dry-run --solver=presto -n demo -c conda-forge python=3.13
```

The service receives installed package records, history, pins, virtual packages,
channel definitions, and the requested solver settings. It never receives the
local prefix path or its file inventory. The result comes back as a final
package state; conda still computes the local unlink/link transaction, including
`--force-reinstall` behavior.

## Stop the service

```bash
conda broker stop conda-presto.server
```

## Boundaries

This is not a stable distributed-solver protocol:

- It discovers only `conda-presto.server` through conda-broker and accepts only
  a ready loopback HTTP endpoint. There is no URL setting, remote mode, or
  authentication mechanism.
- Its `/solver/v1` protocol is hidden from the public HTTP API and has no
  compatibility guarantee. It depends on private `conda-rattler-solver` state.
- Keep the client and service in compatible conda / conda-rattler-solver
  environments. Do not use it as a way to solve against a different conda
  configuration or a remote Docker server.
- Offline mode, `--update-deps`, and conda-build caller-provided indexes are
  rejected because the service cannot reproduce those inputs accurately.
- Successful final states are cached by serialized solver request fields and
  dependency versions. A hit also requires current repodata cache-file markers
  to match those recorded by the worker. Repeating the same operation against
  unchanged request fields can skip index construction and solving; a completed
  transaction normally changes the installed records in the next key. Prefix
  paths and file inventories are not transmitted or keyed.
- Solver cache entries use the configured result-cache memory/file/Redis store
  under a private namespace. They are not available through `/r/<hash>`.

For interface details, see the
[Presto solver reference](../reference/solver-backend.md).
