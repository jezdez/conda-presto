# Refresh cached solver results

The broker-managed service can regularly check recorded Presto solver requests
and recompute missing or stale final-state entries. The scheduler does not run
in ordinary HTTP servers or the Docker image.

Only successful cacheable foreground requests to the private `/solver/v1`
handler are recorded. Public `/resolve` traffic does not populate the recorded
request catalog.

## Configure refresh before broker startup

Stop the broker so its next daemon inherits the settings:

```bash
conda broker stop

export CONDA_PRESTO_SOLVER_CACHE_WARM_INTERVAL_S=300
export CONDA_PRESTO_SOLVER_CACHE_WARM_BATCH_SIZE=8
export CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_SIZE=32

conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

The first cycle starts 30 seconds after the broker child becomes ready. Later
cycles start according to the configured interval.

The scheduler also requires a nonzero result-cache capacity or a persistent
result store. The default in-process capacity is sufficient.

## Record a representative request

Run the same cacheable dry-run twice:

```bash
conda create --dry-run --solver presto --name presto-demo python=3.13
conda create --dry-run --solver presto --name presto-demo python=3.13
```

A request becomes eligible after two successful uses. Records expire after
seven days without another foreground use. Requests are ranked by use count,
then recency, then fingerprint when the bounded catalog is full.

Use the real commands and prefix states that matter to the local workflow. A
different installed state, history, pin set, virtual package set, or solver
setting can select a different final-state cache entry.

## Follow refresh activity

Follow the service log in another terminal:

```bash
conda broker logs conda-presto.server --follow
```

Each completed cycle emits a `Solver cache refresh cycle` line with cumulative
process-local counters. See {doc}`monitor-service` for the field meanings.

Refresh never starts a replay while foreground work is active or waiting. If a
foreground request arrives during a replay, the current replay finishes or
times out and the cycle starts no further candidate.

## Persist recorded requests

Recorded requests are process-local by default. Configure a file or Redis
result store, then enable persistence before starting the broker:

```bash
conda broker stop

export CONDA_PRESTO_RESULT_CACHE_BACKEND=file
export CONDA_PRESTO_RESULT_CACHE_DIR="$HOME/.cache/conda-presto/results"
export CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_PERSIST=true

conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

Candidate persistence requires a file or Redis backend. Requests with detected
channel credentials or tokenized URLs remain memory-only. Treat persisted
requests as private service data because they include installed and channel
state.

Persistence is best effort. The service checkpoints after refresh cycles and
during graceful shutdown, so a crash can lose recent observations.

Redis service processes do not merge their recorded-request catalogs. Run one
broker-managed service per catalog.

## Tune cycle work

`CONDA_PRESTO_SOLVER_CACHE_WARM_BATCH_SIZE` limits how many eligible requests a
cycle considers. For a memory-only result cache, that value is also capped by
the in-process entry limit.

Each inspection or solve has a 30-second ceiling and also respects a lower
`CONDA_PRESTO_SOLVE_TIMEOUT_S`. A cycle stops starting work after 60 seconds.
The refresh worker is created lazily for a cycle, reused within that cycle, and
stopped when the cycle finishes.

Increase the interval or reduce the batch size if refresh work competes for
network, CPU, or repodata cache access on the host.

## Disable scheduled refresh

Restart the broker with a zero interval:

```bash
conda broker stop
export CONDA_PRESTO_SOLVER_CACHE_WARM_INTERVAL_S=0
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

Set `CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_SIZE=0` when requests should not
be recorded at all.

See {doc}`configure-result-cache` for storage and
{doc}`../reference/cache` for cache identity and invalidation.
