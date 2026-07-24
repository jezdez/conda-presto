# Configuration reference

conda-presto reads application settings from environment variables when its
configuration module is imported. Conda settings come from the active conda
context. Command-line flags override only the settings represented by those
flags.

Exact environment-variable names, defaults, and scopes are listed in
{doc}`environment-variables`.

## Configuration loading

Application settings are process configuration. Changing an environment
variable after the process imports conda-presto does not update that process.

Integer settings accept decimal integer strings. Boolean settings accept
`1`, `true`, or `yes` and `0`, `false`, or `no`, without regard to case. Empty
values use the default. Ambiguous boolean values and invalid integers fail
during application import with a configuration error.

Comma-separated list settings strip surrounding whitespace and omit empty
items.

Additional validation rejects:

- negative result-cache entry or memory limits
- an unknown result-cache backend
- negative recorded-request catalog size
- candidate persistence with a memory-only backend
- a negative refresh interval
- a refresh batch size below one
- private solver channel or state limits below one
- repair suggestion, attempt, or time limits below one

## Deployment profiles

| Profile | Process behavior | Public HTTP | Private solver | Scheduled refresh |
|---|---|:---:|:---:|:---:|
| `conda presto` | One-shot direct rattler solve | no | no | no |
| `conda presto --serve` | Litestar server with normal request execution | yes | no | no |
| Docker server | One persistent foreground worker | yes | no | no |
| Broker service | One persistent foreground worker managed by conda-broker | loopback | loopback | yes |

The Docker image sets `CONDA_PRESTO_CONCURRENCY=1` and
`CONDA_PRESTO_PERSISTENT_WORKER=1`. Its command fixes the bind host at
`0.0.0.0`. Its built-in health check always uses container port 8000. See
{doc}`docker-images` before overriding the application port.

The broker provider injects a loopback host, one foreground request slot,
persistent-worker mode, disabled HTTP rate limiting, and enabled conda file
locking. It also supplies the broker service identity and allocated endpoint.
See {doc}`broker-service` for the exact child environment.

## Channels and platforms

`CONDA_PRESTO_CHANNELS` provides HTTP fallback channels when an input and
request do not specify them. The CLI uses it only when conda's effective
channels are empty or exactly `defaults` and no input file supplies channels.
`CONDA_PRESTO_PLATFORMS` supplies startup warmup targets. It does not become the
default target list for an ordinary solve, which uses the host platform when no
platform is requested.

The HTTP server checks requested channels against
`CONDA_PRESTO_ALLOWED_CHANNELS`. The default allowlist is the configured
fallback channel list. Named channels and multichannels are expanded through
conda, then compared by exact credential-free URL, including scheme, authority,
and path.
This prevents another host or an HTTP downgrade from borrowing an approved
canonical channel name. A value of `*` accepts arbitrary caller-selected HTTP
and HTTPS channels, but not local `file://` paths. A local channel must be
listed explicitly. Use the wildcard only inside an appropriate trust and
network-egress boundary.

## Startup and readiness

An ordinary server loads the configured channel and platform metadata during
application startup. This primes conda's on-disk repodata cache, but foreground
requests run in fresh child processes and do not reuse the startup process's
indexes. A persistent-worker deployment starts its worker and waits for that
worker to load reusable indexes instead.

`GET /health` reports HTTP 200 after startup when persistent-worker mode is
disabled. In persistent-worker mode it reports HTTP 503 whenever the worker is
not ready.

The configured channels and platforms affect startup cost. In an ordinary
server they should cover metadata worth priming. In a persistent-worker
deployment they should cover expected traffic without loading index
combinations that the deployment never uses.

## Concurrency

`CONDA_PRESTO_CONCURRENCY` limits simultaneous foreground solve requests.
Ordinary HTTP mode defaults to four. Docker and the broker provider set it to
one because they serialize work through a persistent foreground worker.

`CONDA_PRESTO_WORKERS` controls the process pool used within a multi-platform
request. It does not increase the number of foreground requests admitted by
the HTTP limiter. Raising it can reduce multi-platform wall time while
increasing process and index memory.

Scheduled refresh does not borrow the foreground worker or limiter. It still
checks foreground activity before starting work and stops starting requests
when foreground work arrives.

## Result storage

The result-cache backend is selected in this order when
`CONDA_PRESTO_RESULT_CACHE_BACKEND` is unset:

1. Redis when `CONDA_PRESTO_RESULT_CACHE_REDIS_URL` is set.
2. File storage when `CONDA_PRESTO_RESULT_CACHE_DIR` is set.
3. Memory otherwise.

File storage requires a configured directory. Redis uses
`redis://localhost:6379/0` when the backend is explicitly set to `redis` and no
URL is supplied. The published server image contains the Redis client.

Public resolve and private solver entries share the in-process entry and byte
limits. They use separate persistent key namespaces. See {doc}`cache` for
identity, retention, capacity, failure fallback, and invalidation.

## HTTP middleware and limits

The Litestar application configures:

- request-body size enforcement
- per-client rate limiting when its configured value is nonzero
- CORS only when allowed origins are explicitly configured
- response compression
- structured request logging
- solve and parse time limits in the handlers

See {doc}`cache` for cache-specific deadline behavior.

Behind a reverse proxy, uvicorn must trust the intended proxy addresses through
`--forwarded-allow-ips` before forwarded client addresses can be used for rate
limiting. TLS, authentication, and public network admission remain deployment
responsibilities.

## Development workspace

The Pixi workspace defines these environments:

| Environment | Features | Purpose |
|---|---|---|
| `dev` | development and server | Editing, linting, and local server work |
| `test` | test and server | Pytest, coverage, HTTP clients, and server dependencies |
| `prod` | server | Production HTTP and broker service runtime |
| `redis` | server | Server runtime with Redis support |
| `cli` | base | CLI and composite Action local mode |
| `docs` | documentation | Sphinx and documentation extensions |
| `release` | release | Isolated package-build environment |

The workspace exposes these tasks:

| Task | Command | Purpose |
|---|---|---|
| `lint` | `pixi run lint` | Check Python with Ruff |
| `format` | `pixi run format` | Format Python with Ruff |
| `test` | `pixi run -e test test` | Run pytest with coverage |
| `bench` | `pixi run -e test bench` | Run pytest-benchmark tests |
| `serve` | `pixi run serve` | Start a reload-enabled development server |
| `docs` | `pixi run -e docs docs` | Build the Sphinx site |

Changes to Pixi metadata in `pyproject.toml` require an updated `pixi.lock`.

## Related reference

- {doc}`environment-variables`
- {doc}`docker-images`
- {doc}`broker-service`
- {doc}`cache`
- {doc}`observability`
