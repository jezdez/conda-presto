# Changelog

## 0.5.0 (unreleased)

- Added in-memory LRU result cache with `GET /r/{hash}` permalink endpoint
- Added HMAC-signed solve receipts with `POST /verify` endpoint
- Added `?receipt=true` on `/resolve` to emit `X-Solve-Receipt` header

## 0.4.0

- Added documentation site (Sphinx + MyST + conda-sphinx-theme)
- Added GitHub Pages deployment workflow
- Moved design proposals from `plans/` to `docs/proposals/`
- Added reference, explanation, and tutorial index pages with grid navigation

## 0.3.0

- Added MCP endpoint (`/mcp`) via litestar-mcp
- Added `POST /parse` endpoint for file parsing without solving
- Added `GET /platforms` and `GET /version` endpoints
- Added rate limiting (`CONDA_PRESTO_RATE_LIMIT`)
- Added request body size limit (`CONDA_PRESTO_MAX_BODY_BYTES`)

## 0.2.0

- Added output format routing (`--format` / `?format=`)
- Added raw file upload with Content-Type dispatch
- Added multi-platform parallel solving via `ProcessPoolExecutor`
- Added Docker images (server + CLI)
- Added GitHub Action (local + remote modes)
- Added brotli/gzip compression middleware
- Added `GET /formats` endpoint

## 0.1.0

- Initial release
- CLI: `conda presto` subcommand with `-c`, `-p`, `-f`, `--format`
- HTTP API: `GET /resolve`, `POST /resolve`
- In-memory repodata index caching
- Cross-platform virtual package injection
- conda-rattler-solver integration
