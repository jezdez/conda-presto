# MCP: native MCP server + conda-meta-mcp integration

Status: native MCP implemented; conda-meta-mcp integration not yet started
Owner: TBD
Filed: 2026-04-16
Depends on: [transcoder](01-transcoder.md) (optional), [diff](11-diff.md) (optional).

## TL;DR

Two complementary MCP surfaces for conda-presto:

1. **Native MCP** (implemented) — conda-presto's Litestar app exposes
   its route handlers as MCP tools via `litestar-mcp`. Any MCP client
   can connect directly to a running conda-presto instance at `/mcp`.
2. **conda-meta-mcp wrappers** (not yet started) — thin tool
   wrappers in conda-meta-mcp that call conda-presto's HTTP API,
   so agents already using conda-meta-mcp get solver tools without
   a second MCP connection. Fulfills conda-meta-mcp's roadmap item:

> Planned: Solver feasibility signals (dry-run outputs)

## Motivation

- **Two valid deployment shapes.** Some users run conda-presto as a
  standalone service (native MCP is the right answer). Others already
  run conda-meta-mcp and want solver tools alongside metadata
  queries (conda-meta-mcp wrappers are the right answer). Both
  should exist.
- **Roadmap fit.** "Solver feasibility signals" is the next planned
  tool category in conda-meta-mcp's README. conda-presto is the
  reference implementation.
- **Complementary projects.** conda-meta-mcp owns metadata queries;
  conda-presto owns solving + format translation. Together they
  give agents a complete picture of the conda ecosystem.

## Native MCP (implemented)

conda-presto's Litestar route handlers are annotated with
`mcp_tool` / `mcp_resource` kwargs. The `LitestarMCP` plugin
auto-discovers these at startup and exposes them via MCP Streamable
HTTP at `/mcp`.

Current tools/resources:

| Name | Type | Route | Description |
|---|---|---|---|
| `resolve` | tool | `GET /resolve` | Resolve specs to pinned packages |
| `resolve_file` | tool | `POST /resolve` | Resolve an environment file or inline specs |
| `parse_file` | tool | `POST /parse` | Extract specs/channels from a file without solving |
| `formats` | resource | `GET /formats` | List supported output format names |
| `platforms` | resource | `GET /platforms` | List known conda platform subdirs |
| `version` | resource | `GET /version` | Version info for conda-presto and dependencies |
| `health` | resource | `GET /health` | Liveness probe |

New endpoints (lint, diff, transcode, etc.) automatically become
MCP tools when they carry `mcp_tool=` in their decorator — no
separate MCP module to maintain.

Dependency: `litestar-mcp` (PyPI), installed in the `server` pixi
feature. `pyjwt` is also required (litestar-mcp declares
`litestar[jwt]` but litestar >= 2.21 no longer bundles it).

## conda-meta-mcp wrappers (not yet started)

### API surface (new conda-meta-mcp tools)

### `resolve`

```python
@register_tool
async def resolve(
    specs: list[str] | None = None,
    file_content: str | None = None,
    filename: str | None = None,
    channels: list[str] | None = None,
    platforms: list[str] | None = None,
    format: str | None = None,
) -> dict:
    """Dry-run solve a set of package specs or an environment file.

    Resolves to fully-pinned packages without downloading or installing
    anything. Returns either a structured JSON list (default) or a
    rendered lockfile body when `format` is set (e.g. "pixi.lock",
    "conda-lock-v1", "explicit", "environment-yaml").

    Backed by conda-presto.
    """
```

### `transcode` (after [transcoder](01-transcoder.md) lands)

```python
@register_tool
async def transcode(
    file_content: str,
    filename: str,
    format: str,
    platforms: list[str] | None = None,
) -> str:
    """Convert an environment file from one format to another.

    Lockfile-to-lockfile conversions skip the solver entirely (fast).
    Other conversions re-solve. Returns the rendered output as a string.

    Backed by conda-presto.
    """
```

### `diff` (after [diff](11-diff.md) lands)

```python
@register_tool
async def diff(
    from_specs: list[str] | None = None,
    from_file_content: str | None = None,
    from_filename: str | None = None,
    to_specs: list[str] | None = None,
    to_file_content: str | None = None,
    to_filename: str | None = None,
    platforms: list[str] | None = None,
) -> dict:
    """Diff the resolved package sets of two environments.

    Returns added / removed / changed packages per platform.
    Useful for reviewing dependency changes in PRs or migrations.

    Backed by conda-presto.
    """
```

### Implementation outline

In conda-meta-mcp:

1. New module `conda_meta_mcp/tools/conda_presto.py` (or three
   separate modules — match the existing one-file-per-tool pattern).
2. Configurable backend URL via env var
   `CONDA_META_MCP_PRESTO_URL` (required, no default — users
   provide their own deployment URL).
3. Use `httpx.AsyncClient` with sensible timeouts (default 30s,
   override per-tool). `httpx` is already a conda-meta-mcp
   dependency.
4. Surface conda-presto's HTTP errors as MCP tool errors with the
   sanitized error messages conda-presto already returns.
5. Mark the "Planned: Solver feasibility signals" line in the
   conda-meta-mcp README as done; link to conda-presto.

In conda-presto:

- No further code changes required. The native MCP and HTTP API
  already cover everything conda-meta-mcp needs to call.

## Tests

Native MCP (in conda-presto):

- `test_production_app_has_mcp_plugin` — verifies `LitestarMCP` is
  registered on the production app.
- `test_resolve_handlers_have_mcp_tool_opt` — verifies `mcp_tool`
  opt is set on both resolve handlers.
- `test_health_handler_has_mcp_resource_opt` — verifies `mcp_resource`
  opt is set on the health handler.

conda-meta-mcp wrappers (future):

- Each tool: happy path against a stubbed conda-presto (use
  `respx` or similar to fake HTTP responses).
- Error path: backend returns 400 / 500 → tool surfaces a clean
  MCP error.
- Timeout: backend hangs → tool times out cleanly.

## Effort

- Native MCP: done (~1 hour, decorator kwargs + plugin line +
  dependency wiring).
- conda-meta-mcp wrappers: ~½ day for all three tools (after the
  conda-presto endpoints they wrap exist).

## Open questions

- **Auth.** None today. If conda-meta-mcp ever supports per-tenant
  config, consider a `CONDA_META_MCP_PRESTO_TOKEN` env var for
  hosted tenants behind auth. conda-presto's native MCP inherits
  whatever auth middleware Litestar is configured with.
- **Bundling.** Should there be a `cmm` extras-install that pulls
  conda-presto as a sibling tool for fully local agent setups
  (`pixi global install conda-meta-mcp[presto]`)? Probably yes
  later, but defer until the integration has shaken out.

## Out of scope

- Bidirectional integration (conda-presto calling conda-meta-mcp
  for spec validation suggestions). The [preflight](13-preflight.md) `/preflight`
  endpoint could call conda-meta-mcp's `package_search` for
  "did you mean" — interesting cross-link, but defer.
- Multi-source aggregation (conda-meta-mcp wrapping several
  conda-presto deployments). Premature.

## References

- conda-meta-mcp repo: https://github.com/conda-incubator/conda-meta-mcp
- conda-meta-mcp tool registry pattern:
  `conda_meta_mcp/tools/registry.py` (in the conda-meta-mcp repo)
- Existing tool examples to follow for structure:
  `tools/package_search.py`, `tools/repoquery.py`
- conda-meta-mcp blog post:
  https://conda.org/blog/conda-meta-mcp
- litestar-mcp: https://github.com/cofin/litestar-mcp
