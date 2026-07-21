# Observability reference

conda-presto exposes readiness and version endpoints, application logs, and
conda-broker process diagnostics. It does not expose a metrics endpoint or a
public cache-administration API.

## HTTP endpoints

| Endpoint | Success | Failure | Scope |
|---|---|---|---|
| `GET /health` | HTTP 200 with `{"status":"ok"}` | HTTP 503 with `{"status":"unavailable"}` | Readiness of the configured persistent worker |
| `GET /version` | HTTP 200 | Not applicable | Installed conda-presto and conda versions, plus conda-rattler-solver and conda-lockfiles when their distribution metadata is available |

When persistent-worker mode is disabled, `/health` returns ready after the
application lifespan has started. It does not probe channels, a persistent
result store, or an external Redis server. In persistent-worker mode, it
returns unavailable when the worker is not ready and triggers that server's
configured recovery path.

The Docker health check uses `/health`. The broker service wraps the same
endpoint in its executable health check.

## Application logging

`CONDA_PRESTO_LOG_LEVEL` controls the `conda_presto` logger and defaults to
`INFO`. Litestar records only the request path, method, content type, and
response status for public HTTP access logs. It does not record headers,
cookies, query parameters, request bodies, response headers, or response
bodies. The exact private `/solver/v1` route is excluded from access logging
because its body can contain channel credentials and installed-prefix state.
Operational messages record channel counts rather than raw channel URLs.
Before application and propagated conda log records are emitted, URL-shaped
strings and conda `Current channels:` blocks are replaced. This catches URLs
that embed credentials, but it is not generic secret detection. Keep other
secrets out of specs, filenames, and free-form values that can reach logs.

`conda presto --serve`, including the Docker and broker entrypoints, disables
uvicorn's separate access log so raw query strings are not logged twice. When
starting uvicorn directly, pass `--no-access-log` to preserve this behavior.

Operational log messages cover:

- repodata warmup and persistent-worker startup
- unexpected solve and export failures
- persistent-worker restart and cleanup failures
- persistent result-store read, write, timeout, and corrupt-entry failures
- recorded-request persistence failures
- scheduled solver-cache refresh warnings and cycle summaries

Request-specific refresh warnings contain only the first 12 characters of the
recorded-request fingerprint. The cycle summary contains aggregate counters and
no specs, channel definitions, serialized requests, or complete fingerprints.

## Refresh cycle counters

At `INFO` or a more verbose level, every completed scheduled refresh cycle
emits a message beginning with `Solver cache refresh cycle`. Its
`solver_cache_refresh` log-record field has these integer values:

| Field | Scope |
|---|---|
| `selected` | Eligible requests selected for the current cycle |
| `recorded_requests` | Successful foreground recordings during the process lifetime |
| `cycles` | Cycles started during the process lifetime |
| `already_current` | Inspections or resolve outcomes that found a current entry |
| `attempts` | Background solve attempts started |
| `successful_refreshes` | Background results published or confirmed current after recomputation |
| `foreground_skips` | Cycle stops or skips caused by active, waiting, or newly arrived foreground work |
| `timeouts` | Background inspection, worker startup, or solve timeouts |
| `failures` | Background infrastructure or solver failures |
| `rejected_publications` | Results not accepted by the cache or required persistent store |

All fields except `selected` accumulate from process startup. The counters are
not persisted and are not returned by `/health`.

## Broker diagnostics

conda-broker captures stdout and stderr from the service process. The following
commands are available for `conda-presto.server`:

```bash
conda broker status conda-presto.server
conda broker endpoint conda-presto.server
conda broker logs conda-presto.server --lines 200
conda broker logs conda-presto.server --previous
conda broker logs conda-presto.server --follow
conda broker events --lines 100
conda broker doctor
```

`--previous` includes output from a previous service process. `events` reports
broker lifecycle events, including unhealthy and restart transitions. Add
`--json` to obtain machine-readable output from these commands. `doctor`
reports provider-discovery errors and whether the selected runtime and log
directories are writable.

## Cache visibility

`/resolve` returns a content-addressed `Location` only when its response was
retained. The same location on a later response identifies the same canonical
inputs and repodata markers, but it does not prove whether that request was a
cache hit or a recomputation.

The private solver response has no cache-status header. Recorded requests,
their counts, and scheduler controls are not exposed through HTTP or OpenAPI.
Use aggregate refresh logs and controlled timing measurements when evaluating
cache behavior.

## See also

- {doc}`/reference/http-api`
- {doc}`/reference/broker-service`
- {doc}`/reference/cache`
- {doc}`/reference/environment-variables`
