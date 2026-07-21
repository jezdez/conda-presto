# Use the local solver service

This tutorial introduces the two local integrations added for repeated conda
work. You will start the broker-managed HTTP service, call its public API, and
delegate one conda dry-run to the internal Presto solver backend.

## Verify service discovery

Use the current-main installation from {doc}`../quickstart`, then list the
discovered services from that activated environment:

```bash
conda broker list
```

The output should include `conda-presto.server`. The service is manual and does
not start with ordinary `conda presto` commands.

## Start the service

Start it and wait for its persistent worker to load configured indexes:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
conda broker endpoint conda-presto.server
```

The endpoint command prints a broker-assigned loopback URL. Keep that URL for
the next step.

## Call the public HTTP API

Replace `PORT` with the reported port:

```bash
curl -sS http://127.0.0.1:PORT/resolve \
  --json '{
    "specs": ["python=3.13"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' | jq '.[0] | {platform, error}'
```

The broker service exposes the same public HTTP API as `conda presto --serve`.
Its persistent worker keeps Python, conda, repodata, and solver indexes loaded
between requests.

## Delegate a conda dry-run

The internal solver backend discovers this same service through conda-broker:

```bash
conda create --dry-run --solver=presto \
  -n presto-demo \
  -c conda-forge \
  python=3.13
```

The service computes the final package state. The calling conda process still
computes the local transaction. Without `--dry-run`, package downloads and
prefix changes would also remain in the calling process.

Run the same dry-run again before changing the prefix. Its complete serialized
solver state may reuse the private final-state cache. There is currently no
client header or command output that proves a cache hit, so timing alone is not
a reliable cache diagnostic.

## Inspect and stop the service

Check status and recent logs:

```bash
conda broker status conda-presto.server
conda broker logs conda-presto.server --lines 50
```

Stop the service when finished:

```bash
conda broker stop conda-presto.server
```

## What you learned

- The broker service is opt-in and loopback-only.
- Public HTTP solves and internal conda final-state solves share one service.
- The solver receives the caller's effective installed state and virtual
  packages, while the prefix transaction stays local.
- Docker does not expose the internal solver route or run scheduled solver-cache
  refresh.

For operational commands, see {doc}`../how-to/run-broker-service` and
{doc}`../how-to/use-presto-solver`. Exact contracts are in
{doc}`../reference/broker-service` and {doc}`../reference/solver-backend`.
