# Configuration

This page covers deployment, cache, Docker, and development configuration for
conda-presto.

## Docker images

Two image flavors are published to GitHub Container Registry on every
release, for both `linux/amd64` and `linux/arm64`.

### Server image

The server image starts the HTTP API by default:

```bash
docker run -p 8000:8000 ghcr.io/jezdez/conda-presto:latest
```

The server starts one persistent worker process. On startup, it loads repodata
and indexes for the configured channels and platforms. Single-platform requests
run in that process. For multi-platform requests, the worker coordinates a
persistent process pool whose size is controlled by `CONDA_PRESTO_WORKERS`.
After a solve times out, the server reports unavailable until a replacement
worker has loaded its indexes.

### CLI image

The CLI image runs `conda presto` directly. Pass arguments after the
image name:

```bash
docker run ghcr.io/jezdez/conda-presto:cli -c conda-forge -p linux-64 zlib
docker run ghcr.io/jezdez/conda-presto:cli -f environment.yml -p linux-64
```

### Available tags

| Tag | Image | Description |
|---|---|---|
| `latest` | Server | Most recent server release |
| `<version>` | Server | Specific release (e.g. `0.6.0`) |
| `<major>.<minor>` | Server | Latest patch for a minor (e.g. `0.5`) |
| `<major>` | Server | Latest minor for a major (e.g. `0`) |
| `cli` | CLI | Most recent CLI release |
| `<version>-cli` | CLI | Specific CLI release (e.g. `0.6.0-cli`) |
| `<major>.<minor>-cli` | CLI | Latest CLI patch for a minor |

### Building locally

```bash
docker build -f docker/Dockerfile --target server --build-arg PIXI_ENV=prod -t conda-presto .
docker run -p 8000:8000 conda-presto

docker build -f docker/Dockerfile --target cli --build-arg PIXI_ENV=cli -t conda-presto-cli .
docker run conda-presto-cli -c conda-forge -p linux-64 zlib
```

Both images use a multi-stage build: dependencies are installed with
pixi in the build stage, and only the runtime environment is copied
into a minimal `debian:bookworm-slim` image. Both run as a non-root
user.

## Development setup

conda-presto uses [pixi](https://pixi.sh/) for development. Clone the
repo and install dependencies:

```bash
git clone https://github.com/jezdez/conda-presto.git
cd conda-presto
pixi install
```

The following pixi tasks are available:

```{list-table}
:header-rows: 1
:widths: 20 40 40

* - Task
  - Command
  - Description
* - `lint`
  - `pixi run lint`
  - Check code style with ruff
* - `format`
  - `pixi run format`
  - Auto-format code with ruff
* - `test`
  - `pixi run test`
  - Run tests with pytest (benchmarks disabled)
* - `bench`
  - `pixi run bench`
  - Run benchmarks with pytest-benchmark
* - `serve`
  - `pixi run serve`
  - Start the dev server with uvicorn (auto-reload)
* - `docs`
  - `pixi run -e docs docs`
  - Build Sphinx documentation
```

### Pixi environments

The project defines several pixi environments for different use cases:

`dev`
: Development environment with ruff and server dependencies.

`test`
: Test environment with pytest, httpx, and server dependencies.

`prod`
: Production server environment (server dependencies only).

`cli`
: CLI-only environment without server dependencies.

`docs`
: Documentation build environment with Sphinx and extensions.

## Production deployment

### Running behind a reverse proxy

In production, run conda-presto behind a reverse proxy such as nginx
or Caddy. This gives you TLS termination, static file serving, and
additional request filtering.

Start uvicorn with `--forwarded-allow-ips` so that rate limiting uses
the real client IP instead of the proxy address:

```bash
uvicorn conda_presto.app:app \
  --host 0.0.0.0 \
  --port 8000 \
  --forwarded-allow-ips='*'
```

Or use Docker:

```bash
docker run -p 8000:8000 \
  -e CONDA_PRESTO_HOST=0.0.0.0 \
  -e CONDA_PRESTO_RATE_LIMIT=100 \
  ghcr.io/jezdez/conda-presto:latest
```

### Rate limiting

Rate limiting is enabled by default at 300 requests per minute per
client IP. Adjust with `CONDA_PRESTO_RATE_LIMIT` or set to `0` to
disable it entirely (useful when the reverse proxy handles rate
limiting itself).

### CORS

By default, CORS is disabled. To allow browser clients, set
`CONDA_PRESTO_CORS_ORIGINS` to the frontend domains that should be
allowed:

```bash
export CONDA_PRESTO_CORS_ORIGINS="https://app.example.com,https://ci.example.com"
```

### Request limits

Several variables protect the server from oversized or abusive
requests:

- `CONDA_PRESTO_MAX_BODY_BYTES` caps upload size (default 1 MB)
- `CONDA_PRESTO_MAX_SPECS` caps specs per request (default 200)
- `CONDA_PRESTO_MAX_CHANNELS` caps channels per request (default 8)
- `CONDA_PRESTO_MAX_PLATFORMS` caps platforms per request (default 8)
- `CONDA_PRESTO_MAX_REPAIR_SUGGESTIONS` caps returned repair suggestions (default 5)
- `CONDA_PRESTO_MAX_REPAIR_ATTEMPTS` caps repair candidates evaluated (default 20)
- `CONDA_PRESTO_MAX_REPAIR_TIME_BUDGET_MS` caps repair search time (default 5000 ms)
- `CONDA_PRESTO_SOLVE_TIMEOUT_S` caps solve duration (default 60s)
- `CONDA_PRESTO_PARSE_TIMEOUT_S` caps file parsing duration (default 10s)
- `CONDA_PRESTO_MAX_INDEX_CACHE_ENTRIES` caps in-process solver index
  cache entries (default 128; set to `0` to disable index caching)

The HTTP server accepts channels from `CONDA_PRESTO_ALLOWED_CHANNELS`
(defaulting to `CONDA_PRESTO_CHANNELS`). Set it to `*` only when the
server is already protected by trusted callers and network egress
controls.

See [Environment variables](environment-variables.md) for the full
list and their defaults.

### Cache warmup

On server startup, conda-presto pre-warms repodata caches for the
platforms listed in `CONDA_PRESTO_PLATFORMS` using the channels from
`CONDA_PRESTO_CHANNELS`. This avoids a cold-start penalty on the
first request. Configure these variables to match your expected
workload:

```bash
export CONDA_PRESTO_CHANNELS="conda-forge,bioconda"
export CONDA_PRESTO_PLATFORMS="linux-64,osx-arm64"
```

This generic startup warmup belongs to the foreground worker. Regular solver
cache warming uses exact previously observed requests instead; its dedicated
worker does not pre-build the configured default channel/platform combinations.

### Broker-managed local service

conda-presto registers `conda-presto.server` with
[conda-broker](https://jezdez.github.io/conda-broker/). The manual service binds
to a broker-assigned loopback port. Its conda-presto server runs in
persistent-worker mode, so its worker and process pool retain loaded repodata
and indexes between requests. If the worker fails or times out, `/health`
reports unavailable and conda-broker replaces the server process.

The published Docker server image also enables persistent-worker mode, but it
does not start conda-broker. The server replaces a failed worker itself.

See the [broker-managed local service tutorial](../tutorials/broker-service.md)
for the start, wait, and endpoint commands.

The Docker image leaves the internal Presto solver endpoint disabled and is not
a `conda --solver=presto` target. It does not run scheduled solver-cache
refreshes.

### Result cache

Successful `/resolve` responses and internal `/solver/v1` final states share an
in-process LRU cache. `/resolve` responses use content-addressed entries and are
returned with a `Location: /r/<sha256>` header. Solver responses use private
`solver-v1:` entries and are not exposed through that endpoint. Configure
the total number of retained responses with
`CONDA_PRESTO_RESULT_CACHE_SIZE` (default 256) and the maximum bytes
held in memory with `CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB`
(default 64, `0` disables the byte cap).

Set `CONDA_PRESTO_RESULT_CACHE_DIR` to add a persistent file-backed
cache layer:

```bash
export CONDA_PRESTO_RESULT_CACHE_DIR=/var/cache/conda-presto/results
```

Set `CONDA_PRESTO_RESULT_CACHE_REDIS_URL` to use Redis instead:

```bash
export CONDA_PRESTO_RESULT_CACHE_BACKEND=redis
export CONDA_PRESTO_RESULT_CACHE_REDIS_URL=redis://localhost:6379/0
```

The server still checks the in-process LRU first, then looks up the corresponding
entry in the persistent store before running the solver. Persistent entries
survive server restarts. Redis support is included in the published Docker
server image. Other Python environments require the `redis` optional dependency.

The `/resolve` key includes the normalized specs, ordered channels, target
platforms, output format, conda-presto and solver versions, and local repodata
cache-file markers. See the [Presto solver reference](solver-backend.md) for the
internal solver cache key and invalidation rules.

### Cache-warming candidates

Successful cacheable foreground `/solver/v1` requests are recorded as
cache-warming candidates. Each record stores the serialized request, request
count, and most recent request time. A catalog entry becomes eligible for
refresh after two uses and expires after seven days without another use. Set
`CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_SIZE=0` to disable recording or change
the default 32-entry limit. When that catalog is full, conda-presto also retains
up to the same number of observations outside it. An observation and the
lowest-ranked candidate exchange places when the observation's request count,
then its most recent request time, ranks higher. The displaced candidate keeps
its accumulated count, and the highest-ranked observation fills a catalog slot
when one becomes vacant.

Candidates are process-local and are not served through HTTP or included in
logs. Persistence is disabled by default because requests can contain installed
package and channel state. To persist requests without detected credentials,
configure a file or Redis result-cache backend and set
`CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_PERSIST=true`. Requests with detected
channel credentials or tokenized URLs remain memory-only. Redis deployments do
not merge candidate lists across service processes.

### Scheduled solver-cache refresh

The broker child checks eligible cache-warming candidates every
`CONDA_PRESTO_SOLVER_CACHE_WARM_INTERVAL_S` seconds (default 300). Set the
interval to `0` to disable scheduled refresh. One cycle considers at most
`CONDA_PRESTO_SOLVER_CACHE_WARM_BATCH_SIZE` requests (default 8), further
bounded by the configured result-cache capacity. Refresh starts only when the
private solver endpoint and persistent foreground worker are both enabled, so
normal HTTP servers and the published Docker server remain unchanged. The
interval is measured between cycle starts; a long cycle reduces the following
wait instead of adding permanent polling drift.

The first cycle starts 30 seconds after foreground readiness. One replay uses
the configured solve timeout capped at 30 seconds, and a cycle starts no new
work after its 60-second budget. Transient backoff starts at one polling
interval and is capped at one hour. The broker child uses a 75-second shutdown
grace period so a bounded call and process cleanup can finish.

Litestar owns the service through two ordered lifespan context managers: solver
resources first and the warmer's AnyIO task group second. The resource lifespan
enters the result store before starting its foreground worker. Shutdown runs in
reverse, so scheduling stops, the current refresh finishes or times out, the
dedicated worker stops, and changed candidate state is checkpointed. The
resource lifespan then drains admitted store operations, stops the foreground
worker, and closes the result store.

Each cycle first checks whether a candidate's stable cache slot already matches
a fresh repodata snapshot. A true hit requires no worker. A missing or stale
slot is replayed through one dedicated worker used only by that cycle. Its first
operation is the exact request, including that request's channels, subdirs, and
repodata mode; it does not run the generic default-channel startup warmup. The
warmer never borrows the foreground limiter or worker. It starts no new replay
while foreground work is active or waiting and stops the cycle after the
current bounded replay if foreground work arrives.

Background process and metadata calls use their own one-token AnyIO thread
limiter. Persistent result-store operation admission and completion waits each
use a two-second caller deadline. Reads and writes share one bounded, serialized
queue. An admitted operation continues in order if its caller stops waiting,
preventing an older delayed filesystem operation from overtaking newer state.
Corrupt values are ignored until a later valid write overwrites them. A current
memory entry is not treated as fully warm until required persistence succeeds;
failures are retried with the workload's exponential backoff.

This is regular polling, not push invalidation. Normal conda freshness policy
decides when local JSON or sharded repodata must refresh. A request with
`use_index_cache=True` suppresses JSON TTL refresh during replay exactly as it
does on the foreground path. A deterministic solver failure is suppressed only
while its recorded repodata snapshot is still fresh and unchanged. Once that
snapshot is stale, the request is retried so normal metadata refresh can reveal
a remote channel update. Transient failures use bounded exponential backoff and
never affect foreground responses or `/health`.

The warmer records aggregate in-process cycle statistics and logs one summary
per cycle with the counters rendered in the message. Logs identify a workload
only by a shortened fingerprint; replay
requests, specs, installed records, channel URLs, and credentials are not
logged, and the dedicated warm child suppresses exception tracebacks. There is
no public metrics endpoint or external telemetry feed. The
broker child also restores conda filesystem locking because the foreground and
warming worker processes may access the same repodata cache concurrently.

### Concurrency tuning

Two variables control parallelism:

`CONDA_PRESTO_CONCURRENCY`
: Thread limiter for concurrent solve requests (default 4). Increase
  this if the server handles many simultaneous clients.

`CONDA_PRESTO_WORKERS`
: Process pool size for multi-platform parallel solves within a
  single request (default `min(4, cpu_count)`). Each platform in a
  multi-platform solve runs in its own process.

## See also

- [Environment variables](environment-variables.md)
- [CLI reference](cli.md)
- [HTTP API reference](http-api.md)
