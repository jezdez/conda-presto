# Monitor a running service

conda-presto exposes readiness and capability endpoints. A broker-managed
deployment also exposes process state, events, and logs through conda-broker.

There is currently no Prometheus endpoint or cache administration API.

## Check readiness

Use `/health` for an HTTP server:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
curl --fail --silent --show-error "$CONDA_PRESTO_URL/health"
```

In persistent-worker mode, HTTP 200 means that the worker is ready to solve.
HTTP 503 means it has stopped or is being replaced. Without persistent-worker
mode, the endpoint reports ready whenever the application is running.

## Check Docker health

The server image uses `/health` for its built-in container health check:

```bash
docker inspect \
  --format '{{json .State.Health}}' \
  conda-presto \
  | jq
```

Read application output with:

```bash
docker logs --follow conda-presto
```

## Check broker state

Use the human-readable status for interactive diagnosis:

```bash
conda broker status conda-presto.server
```

Use JSON in scripts:

```bash
conda broker status conda-presto.server --json \
  | jq '.services[0] | {running, ready, health, restart_count, endpoints}'
```

Follow broker lifecycle events separately from service output:

```bash
conda broker events --follow
```

```bash
conda broker logs conda-presto.server --follow
```

Use `--previous` to inspect output retained from the previous service process:

```bash
conda broker logs conda-presto.server --previous --lines 200
```

## Check the deployed capabilities

Use the read-only discovery endpoints to identify what the process has loaded:

```bash
curl --fail --silent --show-error "$CONDA_PRESTO_URL/version" | jq
curl --fail --silent --show-error "$CONDA_PRESTO_URL/formats" | jq
curl --fail --silent --show-error "$CONDA_PRESTO_URL/platforms" | jq
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/openapi.json" \
  | jq '.paths | keys'
```

The private `/solver/v1` route is intentionally absent from OpenAPI.

## Interpret solver refresh logs

The broker child logs one summary after every completed refresh cycle. The
summary includes these cumulative, process-local counters:

| Field | Meaning |
|---|---|
| `selected` | Eligible records selected for this cycle |
| `recorded_requests` | Foreground requests recorded since process start |
| `cycles` | Cycles started since process start |
| `already_current` | Selected entries that did not need a new result |
| `attempts` | Background solves attempted |
| `successful_refreshes` | Results successfully made current |
| `foreground_skips` | Work not started or continued because foreground work arrived |
| `timeouts` | Inspection, worker startup, or solve timeouts |
| `failures` | Metadata, worker, solve, or cleanup failures |
| `rejected_publications` | Results not accepted by persistent or freshness checks |

Counters reset when the service process restarts. Logs do not include recorded
request bodies. The private solver route is excluded from normal request logs.

## Know the current limits

conda-presto does not currently expose:

- Prometheus or OpenMetrics data
- a per-request solver cache hit header
- a recorded-request listing endpoint
- a cache clear or refresh endpoint
- private solver requests in access logs

Use timing only as a performance measurement, not as proof that a particular
request hit the final-state cache. See {doc}`../explanation/performance` for
the costs that remain on a cache hit and {doc}`troubleshoot` for symptom-based
checks. See {doc}`../reference/observability` for the exact endpoint, log, and
counter contracts.
