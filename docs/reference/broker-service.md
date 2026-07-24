# Broker service reference

conda-presto registers one conda-broker provider through the
`conda_broker` entry-point group. `conda-broker>=0.1.1` is a required
conda-presto dependency.

## Service definition

| Property | Value |
|---|---|
| Service name | `conda-presto.server` |
| Summary | `Broker-managed local conda-presto HTTP API` |
| Source | `conda-presto` |
| Runtime | Local process |
| Start policy | `manual` |
| Restart policy | `on-failure` |
| Process | Current Python interpreter with `-m conda_presto.cli --serve` |
| Stop grace period | 75 seconds |
| Endpoint name | `default` |
| Endpoint | HTTP `/` on a broker-assigned loopback port |

The endpoint allocation supplies `CONDA_PRESTO_PORT` and
`CONDA_PRESTO_URL` to the child process. conda-broker also supplies
`CONDA_BROKER_SERVICE_NAME=conda-presto.server`.

The provider applies these child-process overrides:

| Variable | Value |
|---|---|
| `CONDA_PRESTO_HOST` | `127.0.0.1` |
| `CONDA_PRESTO_CONCURRENCY` | `1` |
| `CONDA_PRESTO_RATE_LIMIT` | `0` |
| `CONDA_PRESTO_PERSISTENT_WORKER` | `1` |
| `CONDA_NO_LOCK` | `false` |

Other environment variables are inherited from the broker daemon. Starting an
already-running broker from a shell with different cache or refresh settings
does not replace the daemon environment. Stop the broker before starting it
with new inherited settings.

## Health check

The broker runs the current Python interpreter with
`-m conda_presto.broker`. That command reads `CONDA_PRESTO_URL`, requests
`/health` with a two-second timeout, and succeeds for an HTTP status from 200
through 299. It accepts only a plain HTTP loopback URL and does not use
environment proxies or follow redirects.

| Setting | Value |
|---|---:|
| Check interval | 2 seconds |
| Check timeout | 2 seconds |
| Startup grace period | 120 seconds |

After the startup grace period, an unhealthy service is handled by the
`on-failure` restart policy. The child leaves internal worker recovery to the
broker. This differs from the Docker server, which replaces its worker inside
the server process.

## Command surface

| Command | Meaning |
|---|---|
| `conda broker list` | List discovered services and provider errors |
| `conda broker start conda-presto.server` | Start the broker if needed, then start this service |
| `conda broker wait conda-presto.server --timeout SECONDS` | Wait until the service reports ready |
| `conda broker wait conda-presto.server --start` | Start the broker and service before waiting |
| `conda broker endpoint conda-presto.server` | Print the default endpoint |
| `conda broker status conda-presto.server` | Report process, health, readiness, restart, and endpoint state |
| `conda broker restart conda-presto.server` | Restart this service |
| `conda broker logs conda-presto.server` | Read captured service output |
| `conda broker stop conda-presto.server` | Stop this service |
| `conda broker stop` | Stop all services and the broker |
| `conda broker doctor` | Check discovery and runtime and log directories |

The `list`, `status`, `endpoint`, `wait`, `logs`, `events`, and lifecycle
commands support `--json`. `logs` also supports `--lines`, `--previous`, and
`--follow`. `events` supports `--lines` and `--follow`.

`conda broker enable conda-presto.server` adds the manual service to the set
started with the broker. `disable` removes it. Their `--start` and `--stop`
options apply the state change immediately.

## Boundaries

The service is opt-in and loopback-only by default. Loopback does not
authenticate other users on the same host. The service exposes the public HTTP
API at its reported root. The server process enables the private `/solver/v1`
route because its environment contains
`CONDA_BROKER_SERVICE_NAME=conda-presto.server`. The identity is process state,
not a request credential. The route also requires the broker-assigned host,
JSON media type, no browser `Origin`, and a loopback peer. These checks exclude
ordinary browser requests and unrelated loopback authorities. They do not
authenticate another local process that can discover and reproduce the request.

Starting this service does not alter normal `conda presto` commands. It is not
started by the Docker image.

## See also

- {doc}`/how-to/run-broker-service`
- {doc}`/how-to/use-presto-solver`
- {doc}`/reference/solver-backend`
- {doc}`/reference/observability`
- {doc}`/explanation/security`
