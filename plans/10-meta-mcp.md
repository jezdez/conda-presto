# meta-mcp: conda-meta-mcp integration — expose conda-presto as MCP tools

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: [transcoder](01-transcoder.md) (transcoder, optional), [diff](11-diff.md) (diff, optional).
Lives in two repos: conda-presto (no changes required) and
conda-meta-mcp (new tool wrappers).

## TL;DR

Add `resolve`, `transcode`, and `diff` tools to
[conda-meta-mcp](https://github.com/conda-incubator/conda-meta-mcp)
that wrap the conda-presto HTTP API. Fulfills conda-meta-mcp's own
roadmap item:

> Planned: Solver feasibility signals (dry-run outputs)

This gives AI agents a complete picture of the conda ecosystem:
metadata queries (already there) + solving + format translation
(via conda-presto).

## Motivation

- **Roadmap fit.** "Solver feasibility signals (dry-run outputs)"
  is the next planned tool category in conda-meta-mcp's README
  (`conda-meta-mcp/README.md` line 29). conda-presto is the
  reference implementation.
- **Complementary projects.** conda-meta-mcp is "metadata as a
  queryable surface for agents"; conda-presto is "solving + format
  translation as a queryable surface." Together they cross the
  threshold from "useful API" to "actually agent-shaped tool."
- **Distribution lever.** conda-meta-mcp already has the
  agent-facing distribution channel (FastMCP, GitHub Action,
  `pixi global install`). Wrapping conda-presto into it gets us
  agent users without conda-presto needing its own MCP transport.
- **Each project stays focused.** conda-presto keeps owning
  solver/transcoder logic; conda-meta-mcp keeps owning the
  agent-facing distribution and the MCP transport details.

## API surface (new conda-meta-mcp tools)

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

## Implementation outline

In conda-meta-mcp:

1. New module `conda_meta_mcp/tools/conda_presto.py` (or three
   separate modules — match the existing one-file-per-tool pattern).
2. Configurable backend URL via env var
   `CONDA_META_MCP_PRESTO_URL`, default to the public deployment
   `https://conda-presto.jezdez.dev`.
3. Use `httpx.AsyncClient` with sensible timeouts (default 30s,
   override per-tool). Verify `httpx` is already a conda-meta-mcp
   dependency; add if needed.
4. Surface conda-presto's HTTP errors as MCP tool errors with the
   sanitized error messages conda-presto already returns.
5. Mark the "Planned: Solver feasibility signals" line in the
   conda-meta-mcp README as done; link to conda-presto.

In conda-presto:

- No code changes required. Optionally:
  - Add a brief mention in the README that the public deployment
    is exposed via conda-meta-mcp for AI agent use.
  - Add an `mcp` section to the existing examples gist with a
    sample MCP tool invocation.

## Tests

- Each tool: happy path against a stubbed conda-presto (use
  `respx` or similar to fake HTTP responses).
- Happy path against the live public deployment (skipped by default,
  enabled in a "smoke" CI job).
- Error path: backend returns 400 / 500 → tool surfaces a clean
  MCP error.
- Timeout: backend hangs → tool times out cleanly.

## Effort

- conda-meta-mcp: ~½ day for all three tools (after the
  conda-presto endpoints they wrap exist).
- conda-presto: ~30 min for README updates.

## Open questions

- **Where does the default backend URL point?** The public
  deployment at `https://conda-presto.jezdez.dev` is convenient
  but pins the integration to that host. Acceptable for v1; revisit
  if someone wants conda-meta-mcp to ship with no external
  dependencies (in which case: default to `localhost:8000`,
  document `CONDA_META_MCP_PRESTO_URL`).
- **Auth.** None today. If conda-meta-mcp ever supports
  per-tenant config, consider a `CONDA_META_MCP_PRESTO_TOKEN` env
  var for hosted tenants behind auth.
- **Bundling.** Should there be a `cmm` extras-install that pulls
  conda-presto as a sibling tool for fully local agent setups
  (`pixi global install conda-meta-mcp[presto]`)? Probably yes
  later, but defer until the integration has shaken out.

## Out of scope

- Native MCP support inside conda-presto (running its own MCP
  server). Possible later if there's demand for a single
  install-target solver MCP server, but adds transport surface and
  duplicates conda-meta-mcp's plumbing. Don't do unless asked.
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
- Public conda-presto deployment: https://conda-presto.jezdez.dev
