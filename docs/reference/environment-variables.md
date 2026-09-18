# Environment variable reference

conda-presto reads these settings when its configuration module is imported.
The scope column distinguishes direct solves and HTTP servers.

## Direct solve settings

These settings affect `conda presto` and HTTP resolve operations.

| Variable | Default | Scope | Purpose |
|---|---|---|---|
| `CONDA_PRESTO_CHANNELS` | `conda-forge` | Direct CLI and HTTP | HTTP fallback when no request or input file supplies channels. CLI fallback when conda's effective channels are empty or only `defaults` and no file supplies channels. Also used for server startup warmup. |
| `CONDA_PRESTO_WORKERS` | `min(4, cpu_count)` | Direct multi-platform solves | Process-pool size for platforms within one request. |
| `CONDA_PRESTO_MAX_INDEX_CACHE_ENTRIES` | `128` | Direct solve processes | Maximum retained rattler indexes. Set to `0` to disable index retention. |
| `CONDA_PRESTO_GLIBC_VERSION` | `2.17` | Direct Linux solves | Injected virtual `__glibc` version. |
| `CONDA_PRESTO_LINUX_VERSION` | `5.15` | Direct Linux solves | Injected virtual `__linux` version. |
| `CONDA_PRESTO_OSX_VERSION` | `11.0` | Direct macOS solves | Injected virtual `__osx` version. |
| `CONDA_PRESTO_WIN_VERSION` | `0` | Direct Windows solves | Injected virtual `__win` version. |

Direct CLI and public HTTP solves apply these target-model overrides for each
requested Linux, macOS, or Windows target, including the host's native subdir.
The resulting effective records can also include other virtual-package plugin
detections or overrides, such as CUDA or architecture records. The public
result-cache identity captures those effective records per requested platform.

## HTTP server settings

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_PLATFORMS` | `linux-64,osx-arm64,osx-64` | Platforms whose configured channels are warmed before readiness. This is not the default platform list for a request. |
| `CONDA_PRESTO_ALLOWED_CHANNELS` | value of `CONDA_PRESTO_CHANNELS` | Accepted HTTP request channels after exact resolved-URL comparison. `*` permits HTTP and HTTPS channels but not local file URLs. |
| `CONDA_PRESTO_CONCURRENCY` | `4` | Maximum simultaneous foreground solve requests. Docker sets `1`. |
| `CONDA_PRESTO_MAX_BODY_BYTES` | `1048576` | Maximum HTTP request body. Excess returns HTTP 413. |
| `CONDA_PRESTO_MAX_SPECS` | `200` | Maximum specs in one request. |
| `CONDA_PRESTO_MAX_CHANNELS` | `8` | Maximum channels in one request. |
| `CONDA_PRESTO_MAX_PLATFORMS` | `8` | Maximum platforms in one request. |
| `CONDA_PRESTO_SOLVE_TIMEOUT_S` | `60` | Solver deadline used by HTTP requests. Non-abandoned cache-state inspection can extend observed request duration. |
| `CONDA_PRESTO_PARSE_TIMEOUT_S` | `10` | HTTP file-parse timeout. |
| `CONDA_PRESTO_HOST` | `127.0.0.1` | Default for the `--host` server flag. The Docker command fixes `0.0.0.0`. |
| `CONDA_PRESTO_PORT` | `8000` | Default for the `--port` server flag. The Docker health check follows this setting. |
| `CONDA_PRESTO_PERSISTENT_WORKER` | `false` | Route HTTP solves through one worker that retains loaded repodata and indexes. Set by Docker. |
| `CONDA_PRESTO_RATE_LIMIT` | `300` | Requests per minute per client IP. Set to `0` to disable. |
| `CONDA_PRESTO_CORS_ORIGINS` | unset | Comma-separated browser origins. CORS middleware is absent when unset. |
| `CONDA_PRESTO_LOG_LEVEL` | `INFO` | Application logger level: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |

Request-cap violations return HTTP 400 unless the body limit applies.

## Result cache settings

These settings apply to HTTP applications. One-shot CLI output does not use the HTTP result cache.

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_RESULT_CACHE_SIZE` | `256` | In-process entry limit for resolve responses. |
| `CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` | `64` | Encoded payload limit in MiB. Set to `0` to remove the byte cap. |
| `CONDA_PRESTO_RESULT_CACHE_BACKEND` | automatic | `memory`, `file`, or `redis`. |
| `CONDA_PRESTO_RESULT_CACHE_DIR` | unset | Directory required by the file backend. Selecting no backend but setting this value selects file storage. |
| `CONDA_PRESTO_RESULT_CACHE_REDIS_URL` | unset | Redis URL. Selecting Redis without a URL uses `redis://localhost:6379/0`. Setting this value selects Redis when no backend is explicit. |
| `CONDA_PRESTO_RESULT_CACHE_REDIS_NAMESPACE` | `conda-presto` | Redis key namespace. |

Negative entry or memory limits are rejected. File storage without a directory
and unknown backend names are also rejected. See {doc}`cache` for key and
retention behavior.

## Sigstore settings

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_SIGSTORE_SIGNING_ENABLED` | `false` | Enable signing service-produced artifacts with noninteractive credentials and a configured trust choice. |
| `CONDA_PRESTO_SIGSTORE_ALLOW_PUBLIC_SIGNING` | `false` | Explicitly permit use of the public signing service. |
| `CONDA_PRESTO_SIGSTORE_TRUST_CONFIG` | unset | Path to an operator-provided Sigstore trust configuration. A path alone does not enable signing. |
| `CONDA_PRESTO_SIGSTORE_OFFLINE` | `false` | Select offline verification using the configured trust material. |

Standard installations include SBOM generation, signing and verification support. Signing is disabled by default. Capability discovery reports configuration and provider availability, not a guarantee that signing credentials are currently usable. See {doc}`configuration` for the conda-sigstore requirements.

## Pixi activation settings

The repository's Pixi environments set these conda variables for solve-only
workloads:

| Variable | Value | Effect |
|---|---|---|
| `CONDA_SOLVER` | `rattler` | Select rattler in conda context before conda-presto applies its direct-engine requirement. |
| `CONDA_CHANNEL_PRIORITY` | `strict` | Prefer higher-priority channels. |
| `CONDA_NO_LOCK` | `true` | Disable conda filesystem locking in the shared Pixi activation. The Docker server overrides this to `false`. |
| `CONDA_UNSATISFIABLE_HINTS` | `false` | Disable conda's additional unsatisfiable hint generation. |
| `CONDA_NUMBER_CHANNEL_NOTICES` | `0` | Suppress channel notices. |
| `CONDA_AGGRESSIVE_UPDATE_PACKAGES` | empty | Disable forced aggressive updates. |
| `CONDA_LOCAL_REPODATA_TTL` | `300` | Reuse local repodata under conda's effective cache policy. |
| `CONDA_JSON` | `true` | Suppress progress-oriented conda output. |

The Docker server applies `CONDA_NO_LOCK=false` after its Pixi shell hook because persistent workers share conda's package cache.

## See also

- {doc}`configuration`
- {doc}`cache`
