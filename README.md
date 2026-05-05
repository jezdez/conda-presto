# conda-presto

A fast, dry-run conda solver exposed as both a CLI and an HTTP API.
Given package specs or an environment file (`environment.yml`,
`pixi.toml`, `pyproject.toml`, `requirements.txt`, conda-lock,
pixi-lock, …), it resolves fully pinned packages for one or more
platforms — without downloading or installing anything — and emits
the result as native JSON or any conda exporter format
(`pixi.lock`, `conda-lock.yml`, environment YAML, explicit file, …).

It registers as a conda subcommand plugin (`conda presto`) and
can also run as a standalone HTTP service for integration into
CI pipelines, security scanners, lockfile-conversion workflows, or
other tooling that needs resolved package lists programmatically.

## Features

- Resolve inline specs or environment files (`.yml`, `.yaml`, `.txt`,
  `.lock`, `.toml`, `.json`)
- Full package metadata by default: sha256, md5, urls, sizes, depends
- Cross-platform solving (e.g. solve for `linux-64` from macOS) with
  automatic virtual package injection (`__glibc`, `__linux`, `__osx`)
- Multi-platform solves run in parallel via `ProcessPoolExecutor`
- Single JSON shape across CLI and HTTP API, plus optional routing
  through conda exporter plugins (`--format` / `?format=`)
- Multiple `--file` inputs merged into a single solve
- Conda-native CLI flags (`--override-channels`, `--solver`, `--offline`, etc.)
- HTTP API with JSON input/output (Litestar + uvicorn)
- Interactive API docs at `/` (Scalar UI) with auto-generated OpenAPI schema
- Production middleware: brotli/gzip compression, CORS, request logging, rate limiting
- Repodata caching (on-disk TTL + in-memory index) for ~50x faster repeat solves
- Conda plugin: `conda presto` / `conda presto --serve`
- Uses `conda-rattler-solver` for fast SAT solving

## Install

Install globally with [pixi](https://pixi.sh):

```bash
pixi global install --git https://github.com/jezdez/conda-presto.git
```

For development, clone the repo and install locally:

```bash
git clone https://github.com/jezdez/conda-presto.git
cd conda-presto
pixi install
```

Requires conda >= 25.3 and Python >= 3.13. For best performance,
use `conda-canary/label/dev` which includes the latest solver and
startup optimizations (the pixi workspace is already configured
to use this channel).

## Usage

### As a conda subcommand

```bash
conda presto -c conda-forge -p linux-64 python=3.12 numpy

conda presto -f environment.yml -p linux-64 -p osx-arm64

conda presto -c conda-forge -p linux-64 --format explicit zlib

conda presto --serve --port 8000
```

### Output formats

The default output is a pretty-printed JSON array of results, one
entry per platform, serialized directly from ``SolveResult``
``msgspec.Struct`` instances.  The shape is byte-identical to the
HTTP API's response, so the same JSON parser works for both the
CLI and the API.  Partial failures are expressed as per-platform
``"error"`` fields so a bad spec for one platform doesn't fail the
whole request.

```bash
conda presto -c conda-forge -p linux-64 zlib
```

```json
[
  {
    "platform": "linux-64",
    "packages": [
      {
        "name": "zlib",
        "version": "1.3.2",
        "build": "h25fd6f3_2",
        "build_number": 2,
        "channel": "conda-forge",
        "subdir": "linux-64",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda",
        "sha256": "245c9ee8d688e23661b95e3c6dd7272ca936fabc03d423cdb3cdee1bbcf9f2f2",
        "md5": "c2a01a08fc991620a74b32420e97868a",
        "size": 95931,
        "depends": ["__glibc >=2.17,<3.0.a0", "libzlib 1.3.2 h25fd6f3_2"],
        "constrains": []
      }
    ],
    "error": null
  }
]
```

Pass `--format <name>` to route through conda's exporter plugins
instead (same set of formats as the HTTP API's `?format=` query
parameter).  Because `conda-lockfiles` ships as a base dependency,
the following formats work out of the box:

- `explicit` — `@EXPLICIT` URL-per-line lockfile
- `environment-yaml` / aliases `yaml`, `yml`, `env.yml` — `environment.yml`
- `environment-json` / alias `json` — JSON environment spec
- `requirements` / aliases `reqs`, `txt` — pip-style requirements
- `conda-lock-v1` — `conda-lock.yml`
- `rattler-lock-v6` / alias `pixi-lock-v6` — `pixi.lock`

Any extra formats registered by other installed exporter plugins are
picked up automatically.

```bash
conda presto -c conda-forge -p linux-64 --format explicit zlib
conda presto -c conda-forge -p linux-64 --format conda-lock-v1 zlib
```

#### Pipeline: convert `environment.yml` to a usable `pixi.lock`

Because conda-presto reads any format conda's env-spec plugins
understand and writes any format conda's exporter plugins understand,
the two ends hook together into a plain shell pipeline.  This turns
an `environment.yml` into a `pixi.lock` that `conda env create` can
consume directly — no pixi or other tooling required:

```bash
conda presto -f environment.yml --format pixi-lock-v6 > pixi.lock
conda env create -n demo -f pixi.lock
```

The second step uses `conda-lockfiles`' env-spec loader (registered
through conda's plugin system) so `pixi.lock` is just another input
format to conda.  The same shape works for any other pair of
formats — e.g. `pyproject.toml` &rarr; `conda-lock.yml`,
`conda.yaml` &rarr; `explicit` — because presto is only the glue
between two plugin registries.

On the exporter path, a solver failure on any platform raises the
whole command (the exporter operates on successful environments
only); drop `--format` when you want per-platform partial results.

## HTTP API

Start the server:

```bash
conda presto --serve
# or: uvicorn conda_presto.app:app
```

### `GET /resolve`

Resolve inline specs via query params:

```bash
curl 'http://localhost:8000/resolve?spec=python=3.12&spec=numpy&channel=conda-forge&platform=linux-64'
```

### `POST /resolve`

Resolve specs via a `ResolveRequest` JSON body (`curl --json` sets
`Content-Type: application/json` automatically):

```bash
curl -sS http://localhost:8000/resolve \
  --json '{"specs":["python=3.12","numpy"],"channels":["conda-forge"],"platforms":["linux-64"]}'
```

Upload an environment file directly — no JSON wrapping, no `jq` —
via Content-Type dispatch:

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/resolve?platform=linux-64'
```

Accepted raw-body Content-Types: `application/yaml`,
`application/x-yaml`, `text/yaml`, `application/toml`, `text/plain`.
Use `?filename=` to pick the parser when Content-Type is ambiguous
(e.g. `?filename=pixi.lock` to force the lockfile parser on a
generic YAML upload).

Query params (`spec`, `channel`, `platform`, `format`, `filename`)
work on both GET and POST.  On the JSON dispatch, body fields
override query params when both are present.

By default, returns a JSON array with one entry per platform — the
same shape the CLI emits by default.  Partial failures are expressed
as per-platform `error` fields so a bad spec for one platform doesn't
fail the whole request.

### Output formats (`?format=<name>`)

Pass `?format=` to route the response through conda's exporter plugin
registry — the same formats available to the CLI:

```bash
curl 'http://localhost:8000/resolve?spec=zlib&channel=conda-forge&platform=linux-64&format=explicit'
curl 'http://localhost:8000/resolve?spec=zlib&channel=conda-forge&platform=linux-64&format=conda-lock-v1'
curl 'http://localhost:8000/resolve?spec=zlib&channel=conda-forge&platform=linux-64&format=pixi-lock-v6'
```

Unknown formats return HTTP 400 with the available format list in the
error body.  Because exporters operate on successful `Environment`
objects only, any solver failure on the `?format=` path returns HTTP
500 instead of a partial response — use the default (no `format` query
param) when you want per-platform error details.

Combined with the raw-body upload, the full pipeline
`environment.yml` → remote solve → `pixi.lock` → local
`conda env create` is a plain `curl` one-liner:

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/resolve?format=pixi-lock-v6&platform=linux-64' \
  > pixi.lock
conda env create -n demo -f pixi.lock
```

### `GET /health`

Returns `{"status": "ok"}`.

### `GET /`

Interactive API documentation (Scalar UI). The raw OpenAPI 3.1
schema is available at `/openapi.json`.

## GitHub Action

Use conda-presto directly in CI workflows:

```yaml
- uses: jezdez/conda-presto@v1
  with:
    command: solve
    file: environment.yml
    platforms: linux-64,osx-arm64
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
```

Write a lockfile artifact:

```yaml
- uses: jezdez/conda-presto@v1
  with:
    command: solve
    file: environment.yml
    platforms: linux-64
    format: pixi-lock-v6
    output: pixi.lock
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
```

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `command` | yes | `solve` | Action to perform |
| `file` | no | | Path to an environment file |
| `specs` | no | | Comma-separated package specs |
| `channels` | no | `conda-forge` | Comma-separated channels |
| `platforms` | no | | Comma-separated target platforms |
| `format` | no | | Output format name |
| `output` | no | | Path to write the response body to |
| `endpoint` | yes | | conda-presto base URL |

### Outputs

| Output | Description |
|---|---|
| `result` | The response body |
| `solved` | `true` or `false` |

## MCP (Model Context Protocol)

When running with the `server` feature, conda-presto exposes an MCP
endpoint at `/mcp` via [litestar-mcp](https://github.com/cofin/litestar-mcp).
AI agents can discover and call the `resolve` and `resolve_file` tools
through the standard MCP Streamable HTTP transport.

```bash
# The MCP endpoint is available alongside the REST API
curl http://localhost:8000/.well-known/mcp-server.json
```

Available MCP tools:

| Tool | Description |
|---|---|
| `resolve` | Resolve package specs to fully pinned packages (GET /resolve) |
| `resolve_file` | Resolve an environment file or inline specs (POST /resolve) |

Available MCP resources:

| Resource | Description |
|---|---|
| `health` | Liveness probe (GET /health) |

## Docker

Two image flavors are published to GitHub Container Registry on every
release, for both `linux/amd64` and `linux/arm64`:

- **Server** (`latest`) — starts the HTTP API by default
- **CLI** (`cli`) — runs `conda presto` directly, pass args after the image name

### Server image

```bash
docker run -p 8000:8000 ghcr.io/jezdez/conda-presto:latest
```

The first startup takes ~20-30s while the repodata cache warms up.
Subsequent solves use the in-memory cache and return in milliseconds.

```bash
curl -X POST http://localhost:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{"specs": ["python=3.13"], "platforms": ["linux-64", "osx-arm64"]}'
```

### CLI image

```bash
docker run ghcr.io/jezdez/conda-presto:cli -c conda-forge -p linux-64 zlib

docker run ghcr.io/jezdez/conda-presto:cli -f environment.yml -p linux-64
```

### Available tags

| Tag | Image | Description |
|---|---|---|
| `latest` | Server | Most recent server release |
| `<version>` | Server | Specific release (e.g. `0.2.0`) |
| `<major>.<minor>` | Server | Latest patch for a minor (e.g. `0.2`) |
| `<major>` | Server | Latest minor for a major (e.g. `0`) |
| `cli` | CLI | Most recent CLI release |
| `<version>-cli` | CLI | Specific CLI release (e.g. `0.2.0-cli`) |
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

## Development

```bash
pixi run lint        # ruff check
pixi run format      # ruff format
pixi run test        # pytest (benchmarks disabled)
pixi run bench       # pytest-benchmark only
pixi run serve       # uvicorn with --reload
```

## Performance

### Index caching

Building the repodata index (~700 ms) is the dominant cost of a solve.
Two caches work together:

- **On-disk repodata cache** (conda): how long conda will reuse a
  downloaded ``repodata.json`` before re-fetching from the channel.
  Controlled by ``CONDA_LOCAL_REPODATA_TTL`` (default ``300`` seconds
  in presto's pixi activation).
- **In-memory index cache** (presto): a ``RattlerIndexHelper`` keyed
  by ``(channels, platform)`` that's built on first use and reused for
  the rest of the process's lifetime.  There is no per-entry TTL;
  invalidation happens on process restart or via
  ``clear_index_cache()``.

After the first solve, repeat solves for the same channels/platform
hit the in-memory cache and only pay the SAT solving time (~20-100 ms).

The server pre-warms these caches on startup for the configured
default channels and platforms (see environment variables below).

### Environment variables

#### Application

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_CHANNELS` | `conda-forge` | Comma-separated default channels when none are specified in a request. Also used for cache warmup. |
| `CONDA_PRESTO_PLATFORMS` | `linux-64,osx-arm64,osx-64` | Comma-separated platforms to pre-warm repodata caches for on startup. |
| `CONDA_PRESTO_CONCURRENCY` | `4` | Maximum concurrent solve requests (thread limiter). |
| `CONDA_PRESTO_WORKERS` | `min(4, cpu_count)` | Process pool size for multi-platform parallel solves. |
| `CONDA_PRESTO_MAX_BODY_BYTES` | `1048576` (1 MB) | Maximum request body size in bytes. Returns 413 if exceeded. |
| `CONDA_PRESTO_MAX_SPECS` | `200` | Max specs per request. Returns 400 if exceeded. |
| `CONDA_PRESTO_MAX_PLATFORMS` | `8` | Max platforms per request. Returns 400 if exceeded. |
| `CONDA_PRESTO_SOLVE_TIMEOUT_S` | `60` | Per-request solve timeout in seconds. Returns 504 if exceeded. |
| `CONDA_PRESTO_HOST` | `127.0.0.1` | Default bind address for `--serve` / `--host`. |
| `CONDA_PRESTO_PORT` | `8000` | Default port for `--serve` / `--port`. |
| `CONDA_PRESTO_RATE_LIMIT` | `300` | Max requests per minute per client. Set to `0` to disable. Behind a reverse proxy, start uvicorn with `--forwarded-allow-ips` so the rate-limit key is the real client IP, not the proxy. |
| `CONDA_PRESTO_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins. |
| `CONDA_PRESTO_LOG_LEVEL` | `INFO` | Application log level. |
| `CONDA_PRESTO_GLIBC_VERSION` | `2.17` | Virtual `__glibc` version for cross-platform Linux solves. |
| `CONDA_PRESTO_LINUX_VERSION` | `5.15` | Virtual `__linux` version for cross-platform Linux solves. |
| `CONDA_PRESTO_OSX_VERSION` | `11.0` | Virtual `__osx` version for cross-platform macOS solves. |
| `CONDA_PRESTO_WIN_VERSION` | `0` | Virtual `__win` version for cross-platform Windows solves. |

#### Conda tuning

The following conda environment variables are set via pixi activation
to optimize for a solve-only workload:

| Variable | Value | Purpose |
|---|---|---|
| `CONDA_SOLVER` | `rattler` | Use the fast rattler solver backend |
| `CONDA_CHANNEL_PRIORITY` | `strict` | Skip lower-priority channels early |
| `CONDA_NO_LOCK` | `true` | Skip filesystem locking (single-writer) |
| `CONDA_UNSATISFIABLE_HINTS` | `false` | Skip expensive hint generation on failure |
| `CONDA_NUMBER_CHANNEL_NOTICES` | `0` | No channel notices |
| `CONDA_AGGRESSIVE_UPDATE_PACKAGES` | `""` | No forced updates |
| `CONDA_LOCAL_REPODATA_TTL` | `300` | 5-minute repodata cache TTL |
| `CONDA_JSON` | `true` | Suppress progress output |

### Cross-platform virtual packages

When solving for a foreign platform (e.g. `linux-64` from macOS),
conda needs virtual packages (`__glibc`, `__linux`, `__osx`) to be
present for the target platform. The solver automatically injects
defaults via `context.override_virtual_packages`:

- **linux**: `__glibc` (default `2.17`, conda-forge baseline), `__linux` (default `5.15`)
- **osx**: `__osx` (default `11.0`, Big Sur, conda-forge arm64 baseline)
- **win**: `__win` (default `0`; the package is usually unversioned on conda-forge)

Override these via `CONDA_PRESTO_GLIBC_VERSION`,
`CONDA_PRESTO_LINUX_VERSION`, `CONDA_PRESTO_OSX_VERSION`, and
`CONDA_PRESTO_WIN_VERSION` (see the application environment
variables table above).

## Benchmarks

### End-to-end CLI (hyperfine)

Measured with `hyperfine` on macOS ARM64, Python 3.13,
conda 26.3.2 (canary/dev), `conda-rattler-solver`, warm repodata
cache, solving against `conda-forge`:

| Scenario | Mean | Min | Max |
|---|---:|---:|---:|
| zlib, 1 platform | 0.79 s | 0.77 s | 0.86 s |
| zlib, 3 platforms | 1.34 s | 1.33 s | 1.37 s |
| py+scipy+pandas+matplotlib, 1 platform | 0.94 s | 0.92 s | 1.00 s |
| py+scipy+pandas+matplotlib, 3 platforms | 1.87 s | 1.80 s | 1.94 s |
| py+pytorch+transformers+sklearn (11 pkgs), 1 platform | 1.90 s | 1.88 s | 1.93 s |
| py+pytorch+transformers+sklearn (11 pkgs), 3 platforms | 4.44 s | 4.37 s | 4.52 s |

Times include Python startup (~50 ms), pixi overhead (~50 ms), and
conda import (~200 ms). Multi-platform solves run in parallel and
scale sub-linearly (3 platforms in ~1.7–2.3x the time of 1, depending
on how much of the solve is dominated by per-platform SAT work).

Pipeline scenarios for the format-conversion workflow described
above (`python=3.12 numpy pandas`, single platform):

| Pipeline | Mean | Min | Max |
|---|---:|---:|---:|
| `env.yml` &rarr; `pixi.lock` (presto only) | 0.88 s | 0.87 s | 0.90 s |
| `env.yml` &rarr; `pixi.lock` &rarr; `conda env create --dry-run` | 1.18 s | 1.17 s | 1.19 s |

The second row pays for one extra round-trip through conda's own
solver to validate the lockfile end-to-end.

### In-process server (pytest-benchmark)

With a warm index cache, solves are dominated by SAT time only:

| Operation | Time |
|---|---:|
| Single-platform solve (`zlib`) | ~15 ms |
| Single-platform solve (`python=3.12, numpy`) | ~95 ms |
| `ResolvedPackage.from_record` (single) | ~2.1 µs |
| `ResolvedPackage.from_record` (100 records) | ~222 µs |
| `msgspec.json.encode(ResolvedPackage)` | ~242 ns |
| `msgspec.json.encode(SolveResult)` (100 packages) | ~13 µs |
| Server path: records &rarr; `SolveResult` &rarr; JSON (100 pkgs) | ~234 µs |

Encoding is dominated by `PackageRecord` &rarr; `ResolvedPackage`
conversion (about 95% of the server path time); the actual
`msgspec.json.encode` step is ~5%.

Run benchmarks:

```bash
pixi run bench               # pytest-benchmark
hyperfine 'pixi run conda-presto -c conda-forge -p linux-64 python=3.12 numpy'
```
