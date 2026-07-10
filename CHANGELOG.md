# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `POST /preflight` for deterministic local input validation without solver or channel access.

### Fixed

- Return parser plugin errors as normal HTTP input errors instead of internal server errors.

## [0.5.1] - 2026-07-10

### Fixed

- Published Docker server images now include the Redis client required
  by the Redis result-cache backend.
- Docker server builds now include the package README required by the
  distribution metadata.

## [0.5.0] - 2026-07-10

### Added

- Documentation site with reference, explanation, tutorial, and roadmap
  pages, published through GitHub Pages.
- `POST /transcode` for lockfile-to-lockfile conversion without solving
  when the requested platforms are already present in the input lockfile.
- Content-addressed resolve result cache with stable `Location: /r/{hash}`
  permalinks for successful HTTP solve responses and an optional
  file-backed or Redis-backed persistent layer.

### Changed

- Pixi environments and install docs now use stable conda-forge conda
  packages instead of the `conda-canary/label/dev` channel.
- GitHub Action documentation and examples now focus on CI lockfile
  workflows.
- Proposal documentation is now a concise roadmap page that links to
  GitHub issues for the detailed design records.
- Consolidate the server and CLI container builds into one multi-target
  `docker/Dockerfile`.

### Fixed

- Result cache keys now include the `conda-lockfiles` package version
  so cached lockfile-format responses are invalidated when the exporter
  implementation changes.
- Docker release builds now publish the server `latest` tag on release
  events, matching the documented image tag contract.

### Removed

- Experimental external integration surface and tutorial.
- Generated benchmark result snapshots and the no-signal benchmark CI job.

## [0.4.0] - 2026-04-16

### Added

- `POST /resolve` now dispatches on `Content-Type`: `application/json`
  (or missing) keeps the existing `ResolveRequest` envelope behavior;
  `application/yaml` / `application/x-yaml` / `text/yaml` /
  `application/toml` / `text/plain` treat the body as a raw
  environment file, parsed through conda's env-spec plugin registry.
  Specs, channels, platforms, and format come from query params on
  the raw-body dispatch; `?filename=` picks the parser when
  Content-Type alone is ambiguous.  Unsupported Content-Types return
  HTTP 400 with the list of accepted types.  This makes
  `curl --data-binary @environment.yml -H 'Content-Type: application/yaml'
  '.../resolve?format=pixi-lock-v6&platform=linux-64'` a one-liner —
  no `jq` or JSON wrapping required.
- HTTP API `?format=<name>` query parameter on `GET`/`POST /resolve`
  routes the response through conda's exporter plugin registry.  The
  CLI (`--format`) and HTTP API share the same `render_envs` helper
  so both surfaces expose the same set of formats.  Unknown formats
  return HTTP 400 with the available-format list; solver failures on
  this path return HTTP 500 (exporters can't represent per-platform
  errors).
- `conda-lockfiles >=0.1.1` is now a base dependency, so
  `conda-lock-v1` and `rattler-lock-v6`/`pixi-lock-v6` are available
  out of the box from both CLI and HTTP API.
- Windows virtual package override via `CONDA_PRESTO_WIN_VERSION`
  (default `0`) so cross-platform `win-*` solves get the same
  treatment as Linux/macOS.
- Per-request abuse/DoS caps: `CONDA_PRESTO_MAX_SPECS` (default 200,
  returns 400), `CONDA_PRESTO_MAX_PLATFORMS` (default 8, returns 400),
  and `CONDA_PRESTO_SOLVE_TIMEOUT_S` (default 60s, returns 504).
- Process pool is shut down cleanly via Litestar's `on_shutdown` hook.

### Changed

- HTTP API now returns `SolveResult` / `ResolvedPackage`
  `msgspec.Struct` instances directly; Litestar encodes them natively
  to JSON without an intermediate `to_dict()` conversion.
- CLI default output is now produced by `msgspec.json.encode` on the
  same `list[SolveResult]` the HTTP API returns, pretty-printed via
  `msgspec.json.format(..., indent=2)`.  CLI and HTTP emit identical
  JSON (modulo whitespace), including a per-platform `"error"` field
  (was previously absent from the CLI default).  This is an additive
  change to the schema — correct JSON parsers continue to work.
- POST body fields override query params based on presence, not
  truthiness.  An explicit empty array in the body (e.g.
  `{"platforms": []}`) now overrides the corresponding query param,
  matching the documented behavior.
- Solver error responses surface detail only for an allow-list of
  known exception types (`UnsatisfiableError`, `PackagesNotFoundError`);
  all other exceptions return a generic `"Internal solver error"`
  message.  Full detail is still logged server-side.
- Environment-variable parsing is centralized through `env_int` and
  `env_list` helpers: list values now strip whitespace and drop empty
  parts, and numeric values fail with a clear startup error instead of
  a raw `ValueError` from `int()`.

### Removed

- `resolve-json` conda exporter plugin and the
  `conda_environment_exporters` hook entry.  It duplicated the
  `SolveResult`/`ResolvedPackage` msgspec shape for no good reason;
  the CLI now produces that shape natively.  Minor user-visible
  consequences:
  * `conda presto --format resolve-json ...` now errors with
    "Unknown format".  Fix: drop `--format`; the default output is
    the same JSON shape (plus a `"error"` field per platform).
  * HTTP `GET`/`POST /resolve?format=resolve-json` returns HTTP 400
    for the same reason.  Drop the query parameter.
  * `conda env export --format resolve-json` (invoked from plain
    conda on an installed prefix, with conda-presto installed) no
    longer works.  This was never conda-presto's intended use case.
- `ResolvedPackage.to_dict` and `SolveResult.to_dict` methods: no
  longer called anywhere; msgspec serializes the structs directly.

### Fixed

- HTTP `Content-Type` on `?format=` responses is now derived from the
  exporter's `default_filenames` rather than a hand-coded format-name
  table.  YAML-ish formats (`environment-yaml`, `conda-lock-v1`,
  `rattler-lock-v6`/`pixi-lock-v6`) are now served as
  `application/yaml` instead of `text/plain`.
- Tempfile lifetime bug in `parse_file_content`: ``specs`` and
  ``channels`` are now extracted while the temp file is still open,
  preventing races with env-spec plugins that read the file lazily on
  attribute access.
- README documentation of the index-cache TTL was inaccurate.  The
  on-disk repodata TTL (`CONDA_LOCAL_REPODATA_TTL`) and the in-memory
  `RattlerIndexHelper` cache (no TTL, process-lifetime) are now
  described separately.

## [0.3.0] - 2026-04-16

### Added

- Interactive API documentation (Scalar UI) at `/` with auto-generated
  OpenAPI 3.1 schema at `/openapi.json`, replacing the hand-written
  ~190-line schema dictionary.
- Production middleware: brotli compression with gzip fallback,
  CORS (configurable via `CONDA_PRESTO_CORS_ORIGINS`, default `*`),
  structured request logging, and rate limiting (configurable via
  `CONDA_PRESTO_RATE_LIMIT`, default 300 req/min, `0` to disable).
- New environment variables: `CONDA_PRESTO_RATE_LIMIT`,
  `CONDA_PRESTO_CORS_ORIGINS`, `CONDA_PRESTO_LOG_LEVEL`.
- Native request body size enforcement via Litestar's
  `request_max_body_size` (configurable via
  `CONDA_PRESTO_MAX_BODY_BYTES`, default 1 MB).

### Changed

- Migrated HTTP API framework from Starlette to Litestar. Typed
  request validation via `ResolveRequest` dataclass replaces manual
  JSON parsing.
- Migrated `ResolvedPackage` and `SolveResult` from `dataclasses`
  to `msgspec.Struct` for faster serialization and lower memory.
  Litestar encodes these natively to JSON without intermediate dicts.
- Startup initialization uses Litestar's `on_startup` hook with
  `app.state` instead of module-level globals.
- Application log level configurable via `CONDA_PRESTO_LOG_LEVEL`
  (default `INFO`).
- Renamed project from `conda-resolve` to `conda-presto`
  (package, CLI subcommand, environment variables, Docker images).

## [0.2.1] - 2026-04-16

### Added

- Separate Docker image flavors: server (`latest`) and CLI (`cli`),
  built from `docker/server.Dockerfile` and `docker/cli.Dockerfile`.
  The CLI image excludes server dependencies (uvicorn) for a smaller
  footprint.

## [0.2.0] - 2026-04-16

### Added

- Configuration module (`config.py`) with environment variable overrides
  for all operational settings: `CONDA_PRESTO_CHANNELS`,
  `CONDA_PRESTO_PLATFORMS`, `CONDA_PRESTO_CONCURRENCY`,
  `CONDA_PRESTO_WORKERS`, `CONDA_PRESTO_MAX_BODY_BYTES`,
  `CONDA_PRESTO_HOST`, `CONDA_PRESTO_PORT`,
  `CONDA_PRESTO_GLIBC_VERSION`, `CONDA_PRESTO_LINUX_VERSION`,
  `CONDA_PRESTO_OSX_VERSION`.
- Support for `.toml` and `.json` file extensions in the HTTP API
  file upload endpoint.
- Dynamic OpenAPI schema version derived from package metadata
  via `importlib.metadata`.

### Changed

- Default fallback channel changed from `defaults` to `conda-forge`
  in both CLI and HTTP API, configurable via `CONDA_PRESTO_CHANNELS`.
- Unified HTTP API to single `/resolve` endpoint supporting both
  GET (query params) and POST (JSON body). Removed old `/solve`,
  `/solve/environment-yml`, and `/cache/clear` endpoints.
- Request body uses `specs` field instead of `dependencies`, and
  `file`/`filename` fields for environment file content instead of
  raw YAML body.
- POST body fields override query params when both are present.
- Virtual package versions for cross-platform solving are now
  configurable via environment variables instead of hardcoded.
- Process pool size and server host/port defaults are now
  configurable via environment variables.
- Updated test suite to match the new `/resolve` API.

## [0.1.1] - 2026-04-15

### Added

- Minimal, hardened Docker image using multi-stage build with
  `debian:bookworm-slim` and non-root user.
- Docker release workflow publishing multi-arch images (linux/amd64,
  linux/arm64) to GitHub Container Registry on release and manual
  dispatch.
- Dependabot configuration for Docker base image updates.
- Automated pixi lockfile update workflow (monthly, via
  `pixi-diff-to-markdown`).
- `linux-aarch64` platform support.

### Fixed

- Default platform detection after server warmup. The warmup loop
  left a stale platform in `context._cache_`, causing the server to
  default to the wrong platform. Now captured once at import time
  as `NATIVE_SUBDIR`.

## [0.1.0] - 2026-04-15

### Added

- Core solver module (`resolve.py`) with `ResolvedPackage` and
  `SolveResult` dataclasses using `slots=True` for memory efficiency.
- Split architecture: CLI uses conda's `Environment` model directly
  via `solve_environments()`, server uses lightweight custom types
  via `solve()` for fast JSON serialization.
- Shared `dispatch()` helper for single- and multi-platform solving,
  with per-platform error handling for the server path.
- Multi-platform parallel solving via `ProcessPoolExecutor` with
  persistent worker pool for cross-request cache reuse.
- Cross-platform virtual package injection (`configure_platform()`)
  using `context._cache_` for thread-safe platform configuration
  with conservative defaults (glibc 2.17, linux 5.15, osx 11.0).
- Thread-safe solver invocation via `platform_lock` to prevent
  concurrent requests from racing on process-global state.
- In-memory repodata index cache with `threading.Lock`-guarded
  check-then-build for thundering herd protection, and explicit
  `clear_index_cache()` invalidation.
- HTTP API (`app.py`) built on Starlette with `/solve`,
  `/solve/environment-yml`, `/health`, and `/cache/clear` endpoints.
- `anyio.CapacityLimiter` to cap concurrent solver threads and
  `abandon_on_cancel=True` for client disconnect handling.
- Request body size limit (1 MB), input type validation (both JSON
  and YAML endpoints), Content-Length header validation, and
  sanitized error responses (no internal stack traces leaked).
- Repodata cache pre-warming on server startup via `warmup()`,
  offloaded from the event loop with `anyio.to_thread.run_sync`.
- CLI with resolve as the default action and `--serve` flag for the
  HTTP server. Uses conda's environment specifier plugins for input
  and exporter plugins for output.
- Custom `resolve-json` environment exporter providing full package
  metadata (sha256, md5, urls, sizes, dependencies) as the default
  CLI output format.
- Conda subcommand plugin (`conda presto`) with lazy imports
  to keep plugin load under 1 ms.
- Lazy uvicorn import in `cmd_serve()` to reduce CLI startup
  overhead by ~100 ms for non-server invocations.
- Performance tuning via pixi activation env: `CONDA_SOLVER=rattler`,
  `CONDA_CHANNEL_PRIORITY=strict`,
  `CONDA_NO_LOCK=true`, `CONDA_UNSATISFIABLE_HINTS=false`,
  `CONDA_NUMBER_CHANNEL_NOTICES=0`, `CONDA_AGGRESSIVE_UPDATE_PACKAGES=""`,
  `CONDA_LOCAL_REPODATA_TTL=300`, `CONDA_JSON=true`.
- Comprehensive test suite with pytest, pytest-benchmark, pytest-cov,
  and httpx for async API testing. 99% code coverage with a 95%
  `fail_under` threshold enforced on every run.
- Hyperfine benchmark fixtures (`benchmarks/`) for end-to-end CLI
  performance tracking.
- GitHub Actions CI workflow with lint, test, and benchmark jobs.
- Dependabot configuration for GitHub Actions version updates.
- BSD 3-Clause license.

[Unreleased]: https://github.com/jezdez/conda-presto/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/jezdez/conda-presto/releases/tag/v0.5.0
[0.4.0]: https://github.com/jezdez/conda-presto/releases/tag/v0.4.0
[0.3.0]: https://github.com/jezdez/conda-presto/releases/tag/v0.3.0
[0.2.1]: https://github.com/jezdez/conda-presto/releases/tag/v0.2.1
[0.2.0]: https://github.com/jezdez/conda-presto/releases/tag/v0.2.0
[0.1.1]: https://github.com/jezdez/conda-presto/releases/tag/v0.1.1
[0.1.0]: https://github.com/jezdez/conda-presto/releases/tag/v0.1.0
