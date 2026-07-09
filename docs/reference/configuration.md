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

The first startup can take longer while the repodata cache warms up.
Subsequent solves can reuse the warm repodata and in-memory solver
indexes.

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
| `<version>` | Server | Specific release (e.g. `0.5.0`) |
| `<major>.<minor>` | Server | Latest patch for a minor (e.g. `0.5`) |
| `<major>` | Server | Latest minor for a major (e.g. `0`) |
| `cli` | CLI | Most recent CLI release |
| `<version>-cli` | CLI | Specific CLI release (e.g. `0.5.0-cli`) |
| `<major>.<minor>-cli` | CLI | Latest CLI patch for a minor |

### Building locally

```bash
docker build -f docker/server.Dockerfile -t conda-presto .
docker run -p 8000:8000 conda-presto

docker build -f docker/cli.Dockerfile -t conda-presto-cli .
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

### Hugging Face Spaces

The repository root contains a Dockerfile and Space metadata in
`README.md` for deploying conda-presto as a Hugging Face Docker Space.
The Space listens on port `7860`, runs as user ID `1000`, and starts
the HTTP API with conservative single-worker defaults.

See [Deploy on Hugging Face Spaces](../tutorials/hugging-face.md) for
the full workflow.

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

By default, all origins are allowed (`CONDA_PRESTO_CORS_ORIGINS=*`).
In production, restrict this to your frontend domains:

```bash
export CONDA_PRESTO_CORS_ORIGINS="https://app.example.com,https://ci.example.com"
```

### Request limits

Several variables protect the server from oversized or abusive
requests:

- `CONDA_PRESTO_MAX_BODY_BYTES` caps upload size (default 1 MB)
- `CONDA_PRESTO_MAX_SPECS` caps specs per request (default 200)
- `CONDA_PRESTO_MAX_PLATFORMS` caps platforms per request (default 8)
- `CONDA_PRESTO_SOLVE_TIMEOUT_S` caps solve duration (default 60s)

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

### Result cache

Successful `/resolve` responses are stored in a content-addressed
in-process LRU cache and returned with a `Location: /r/<sha256>`
header. Configure the maximum number of retained responses with
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

The server still checks the in-process LRU first, then looks up the
same content-addressed key in the persistent store before running the
solver. Persistent entries survive server restarts and are stored under
the same CAS key used by `/r/<sha256>`. Redis support requires the
`redis` optional dependency; in Pixi, use the `redis` environment or
include the `redis` feature.

The key includes the normalized specs, ordered channels, target
platforms, output format, conda-presto and solver versions, and local
repodata cache file markers. Repodata refreshes therefore produce new
keys instead of reusing stale solve results.

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
