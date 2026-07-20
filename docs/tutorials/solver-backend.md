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
package state. conda still computes the local unlink/link transaction, including
`--force-reinstall` behavior.

## Stop the service

```bash
conda broker stop conda-presto.server
```

## Boundaries

This is a private, loopback-only protocol. It is not compatible with remote or
Docker servers and does not support offline mode, `--update-deps`, or
conda-build caller-provided indexes.

For protocol, compatibility, and cache details, see the
[Presto solver reference](../reference/solver-backend.md).
