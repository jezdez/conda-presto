# Environment variable reference

conda-presto reads these settings when its configuration module is imported.
The scope column distinguishes direct solves, public HTTP servers, and the
broker-only solver service.

## Direct solve settings

These settings affect `conda presto` and public HTTP resolve operations. The
internal `conda --solver=presto` path captures effective state from the calling
conda process instead.

| Variable | Default | Scope | Purpose |
|---|---|---|---|
| `CONDA_PRESTO_CHANNELS` | `conda-forge` | Direct CLI and HTTP | HTTP fallback when no request or input file supplies channels. CLI fallback when conda's effective channels are empty or only `defaults` and no file supplies channels. Also used for server startup warmup. |
| `CONDA_PRESTO_WORKERS` | `min(4, cpu_count)` | Direct multi-platform solves | Process-pool size for platforms within one request. |
| `CONDA_PRESTO_MAX_INDEX_CACHE_ENTRIES` | `128` | Direct solve processes | Maximum retained rattler indexes. Set to `0` to disable index retention. |
| `CONDA_PRESTO_GLIBC_VERSION` | `2.17` | Direct Linux solves | Injected virtual `__glibc` version. |
| `CONDA_PRESTO_LINUX_VERSION` | `5.15` | Direct Linux solves | Injected virtual `__linux` version. |
| `CONDA_PRESTO_OSX_VERSION` | `11.0` | Direct macOS solves | Injected virtual `__osx` version. |
| `CONDA_PRESTO_WIN_VERSION` | `0` | Direct Windows solves | Injected virtual `__win` version. |

Direct CLI and public HTTP solves apply these target-model overrides for every
Linux, macOS, or Windows target, including the host's native subdir. They do
not reuse host-detected virtual packages. The internal Presto solver does not
use these four defaults. It serializes and restores the calling conda process's
effective virtual package records, including overrides such as CUDA. Those
records also participate in private solver cache identity.

## HTTP server settings

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_PLATFORMS` | `linux-64,osx-arm64,osx-64` | Platforms whose configured channels are warmed before readiness. This is not the default platform list for a request. |
| `CONDA_PRESTO_ALLOWED_CHANNELS` | value of `CONDA_PRESTO_CHANNELS` | Accepted HTTP request channels. `*` permits any channel. |
| `CONDA_PRESTO_CONCURRENCY` | `4` | Maximum simultaneous foreground solve requests. Docker and the broker provider set `1`. |
| `CONDA_PRESTO_MAX_BODY_BYTES` | `1048576` | Maximum HTTP request body. Excess returns HTTP 413. |
| `CONDA_PRESTO_MAX_SPECS` | `200` | Maximum specs in one request. |
| `CONDA_PRESTO_MAX_CHANNELS` | `8` | Maximum channels in one request. |
| `CONDA_PRESTO_MAX_PLATFORMS` | `8` | Maximum platforms in one request. |
| `CONDA_PRESTO_MAX_REPAIR_SUGGESTIONS` | `5` | Server ceiling for returned repair suggestions. |
| `CONDA_PRESTO_MAX_REPAIR_ATTEMPTS` | `20` | Server ceiling for evaluated repair candidates. |
| `CONDA_PRESTO_MAX_REPAIR_TIME_BUDGET_MS` | `5000` | Server ceiling for repair wall time in milliseconds. |
| `CONDA_PRESTO_SOLVE_TIMEOUT_S` | `60` | HTTP solve timeout. Also sets the internal solver client and scheduled refresh ceiling where lower than their fixed limits. |
| `CONDA_PRESTO_PARSE_TIMEOUT_S` | `10` | HTTP file-parse timeout. |
| `CONDA_PRESTO_HOST` | `127.0.0.1` | Default for the `--host` server flag. The Docker command fixes `0.0.0.0`, and the broker provider fixes `127.0.0.1`. |
| `CONDA_PRESTO_PORT` | `8000` | Default for the `--port` server flag. The broker assigns it. The Docker health check remains fixed to port 8000. |
| `CONDA_PRESTO_PERSISTENT_WORKER` | `false` | Route HTTP solves through one worker that retains loaded repodata and indexes. Set by Docker and the broker provider. |
| `CONDA_PRESTO_RATE_LIMIT` | `300` | Requests per minute per client IP. Set to `0` to disable. The broker provider sets `0`. |
| `CONDA_PRESTO_CORS_ORIGINS` | unset | Comma-separated browser origins. CORS middleware is absent when unset. |
| `CONDA_PRESTO_LOG_LEVEL` | `INFO` | Application logger level: `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |

Request-cap violations return HTTP 400 unless the body limit applies. Repair
query values below the configured ceilings narrow one request. Values above the
ceilings are clamped.

## Result cache settings

These settings apply to any HTTP application process, including Docker and the
broker service. One-shot CLI output does not use the HTTP result cache.

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_RESULT_CACHE_SIZE` | `256` | Combined in-process entry limit for public resolve responses and private solver final states. |
| `CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` | `64` | Combined encoded payload limit in MiB. Set to `0` to remove the byte cap. |
| `CONDA_PRESTO_RESULT_CACHE_BACKEND` | automatic | `memory`, `file`, or `redis`. |
| `CONDA_PRESTO_RESULT_CACHE_DIR` | unset | Directory required by the file backend. Selecting no backend but setting this value selects file storage. |
| `CONDA_PRESTO_RESULT_CACHE_REDIS_URL` | unset | Redis URL. Selecting Redis without a URL uses `redis://localhost:6379/0`. Setting this value selects Redis when no backend is explicit. |
| `CONDA_PRESTO_RESULT_CACHE_REDIS_NAMESPACE` | `conda-presto` | Redis key namespace. |

Negative entry or memory limits are rejected. File storage without a directory
and unknown backend names are also rejected. See {doc}`cache` for key and
retention behavior.

## Broker-only refresh settings

These settings are loaded by every server process, but scheduled refresh is
enabled only for the persistent `conda-presto.server` broker child.

| Variable | Default | Purpose |
|---|---|---|
| `CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_SIZE` | `32` | Maximum recorded requests in the candidate catalog, plus the same observation capacity. Set to `0` to disable recording. |
| `CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_PERSIST` | `false` | Best-effort checkpointing of requests without detected credentials. Requires file or Redis storage. |
| `CONDA_PRESTO_SOLVER_CACHE_WARM_INTERVAL_S` | `300` | Seconds between scheduled cycles. Set to `0` to disable scheduling. |
| `CONDA_PRESTO_SOLVER_CACHE_WARM_BATCH_SIZE` | `8` | Maximum eligible requests considered per cycle. Must be positive. |

The first cycle has a fixed 30-second delay. Fixed per-operation and cycle
budgets are documented in {doc}`cache`.

## Broker-injected variables

conda-broker supplies these values to the service child. They are integration
state, not normal user configuration.

| Variable | Value | Consumer |
|---|---|---|
| `CONDA_BROKER_SERVICE_NAME` | `conda-presto.server` | Enables the guarded private solver route and scheduled refresh. |
| `CONDA_PRESTO_URL` | Allocated loopback endpoint | Broker health-check command and service consumers. |
| `CONDA_PRESTO_PORT` | Allocated port | Uvicorn bind configuration. |

The provider also overrides host, concurrency, rate limiting,
persistent-worker mode, and conda file locking. See {doc}`broker-service`.

## Pixi activation settings

The repository's Pixi environments set these conda variables for solve-only
workloads:

| Variable | Value | Effect |
|---|---|---|
| `CONDA_SOLVER` | `rattler` | Select rattler in conda context before conda-presto applies its direct-engine requirement. |
| `CONDA_CHANNEL_PRIORITY` | `strict` | Prefer higher-priority channels. |
| `CONDA_NO_LOCK` | `true` | Disable conda filesystem locking in the shared Pixi activation. The Docker server and broker child override this to `false`. |
| `CONDA_UNSATISFIABLE_HINTS` | `false` | Disable conda's additional unsatisfiable hint generation. |
| `CONDA_NUMBER_CHANNEL_NOTICES` | `0` | Suppress channel notices. |
| `CONDA_AGGRESSIVE_UPDATE_PACKAGES` | empty | Disable forced aggressive updates. |
| `CONDA_LOCAL_REPODATA_TTL` | `300` | Reuse local repodata under conda's effective cache policy. |
| `CONDA_JSON` | `true` | Suppress progress-oriented conda output. |

The Docker server applies `CONDA_NO_LOCK=false` after its Pixi shell hook, and
the broker child injects the same value. Their persistent worker processes
share conda's package cache. The one-shot CLI image keeps the Pixi activation
default.

## See also

- {doc}`configuration`
- {doc}`cache`
- {doc}`broker-service`
- {doc}`solver-backend`
