# Environment variables

conda-presto reads two groups of environment variables: application
variables that control its own behavior, and conda tuning variables
that optimize conda for a solve-only workload.

## Application variables

These variables configure conda-presto itself. They apply to both the
CLI and the HTTP server.

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_CHANNELS` | `conda-forge` | Comma-separated default channels when none are given in a request. Also used for cache warmup on server startup. |
| `CONDA_PRESTO_PLATFORMS` | `linux-64,osx-arm64,osx-64` | Comma-separated platforms to pre-warm repodata caches for on server startup. |
| `CONDA_PRESTO_CONCURRENCY` | `4` | Maximum concurrent solve requests (thread limiter). |
| `CONDA_PRESTO_WORKERS` | `min(4, cpu_count)` | Process pool size for multi-platform parallel solves. |
| `CONDA_PRESTO_MAX_BODY_BYTES` | `1048576` (1 MB) | Maximum request body size in bytes. Returns HTTP 413 if exceeded. |
| `CONDA_PRESTO_MAX_SPECS` | `200` | Maximum number of specs per request. Returns HTTP 400 if exceeded. |
| `CONDA_PRESTO_MAX_PLATFORMS` | `8` | Maximum number of platforms per request. Returns HTTP 400 if exceeded. |
| `CONDA_PRESTO_SOLVE_TIMEOUT_S` | `60` | Per-request solve timeout in seconds. Returns HTTP 504 if exceeded. |
| `CONDA_PRESTO_HOST` | `127.0.0.1` | Default bind address for `--serve` / `--host`. |
| `CONDA_PRESTO_PORT` | `8000` | Default port for `--serve` / `--port`. |
| `CONDA_PRESTO_RATE_LIMIT` | `300` | Maximum requests per minute per client IP. Set to `0` to disable. Behind a reverse proxy, start uvicorn with `--forwarded-allow-ips` so the rate-limit key is the real client IP, not the proxy. |
| `CONDA_PRESTO_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins. |
| `CONDA_PRESTO_LOG_LEVEL` | `INFO` | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `CONDA_PRESTO_GLIBC_VERSION` | `2.17` | Virtual `__glibc` version injected for cross-platform Linux solves. |
| `CONDA_PRESTO_LINUX_VERSION` | `5.15` | Virtual `__linux` version injected for cross-platform Linux solves. |
| `CONDA_PRESTO_OSX_VERSION` | `11.0` | Virtual `__osx` version injected for cross-platform macOS solves. |
| `CONDA_PRESTO_WIN_VERSION` | `0` | Virtual `__win` version injected for cross-platform Windows solves. |

### Virtual package overrides

When solving for a foreign platform (e.g. `linux-64` from macOS),
conda needs virtual packages (`__glibc`, `__linux`, `__osx`, `__win`)
to be present for the target. conda-presto automatically injects
sensible defaults via `context.override_virtual_packages`:

- Linux targets: `__glibc` at 2.17 (the conda-forge baseline) and
  `__linux` at 5.15
- macOS targets: `__osx` at 11.0 (Big Sur, the conda-forge arm64
  baseline)
- Windows targets: `__win` at 0 (usually unversioned on conda-forge)

Override these defaults with the `CONDA_PRESTO_GLIBC_VERSION`,
`CONDA_PRESTO_LINUX_VERSION`, `CONDA_PRESTO_OSX_VERSION`, and
`CONDA_PRESTO_WIN_VERSION` variables.

## Conda tuning variables

The following conda environment variables are set via pixi activation
to optimize for a solve-only workload. They apply to all environments
in the conda-presto pixi workspace.

| Variable | Value | Purpose |
|---|---|---|
| `CONDA_SOLVER` | `rattler` | Use the fast rattler solver backend. |
| `CONDA_CHANNEL_PRIORITY` | `strict` | Skip lower-priority channels early during solving. |
| `CONDA_NO_LOCK` | `true` | Skip filesystem locking (safe for single-writer processes). |
| `CONDA_UNSATISFIABLE_HINTS` | `false` | Skip expensive hint generation on solver failures. |
| `CONDA_NUMBER_CHANNEL_NOTICES` | `0` | Suppress channel notices. |
| `CONDA_AGGRESSIVE_UPDATE_PACKAGES` | `""` | Disable forced package updates. |
| `CONDA_LOCAL_REPODATA_TTL` | `300` | Reuse downloaded repodata for 5 minutes before re-fetching. |
| `CONDA_JSON` | `true` | Suppress progress bars and human-readable output. |

These are configured in the `[tool.pixi.activation.env]` section of
`pyproject.toml`. You can override any of them in your shell before
running conda-presto.

## See also

- [CLI reference](cli.md)
- [Configuration](configuration.md)
