# AGENTS.md — conda-presto coding guidelines

## Project structure

- `conda_presto/` is a flat package (no subpackages). Each module
  owns one concern:
  - `app.py` — Litestar HTTP API: route handlers, middleware config,
    request parsing, the `run_solve` coroutine, and the `Litestar`
    app instance.
  - `resolve.py` — Core solving logic: `run_solver` (drives
    conda-rattler-solver), `solve` (returns `SolveResult` structs),
    `solve_environments` (returns `Environment` objects for
    exporters), multi-platform dispatch via `ProcessPoolExecutor`,
    index caching, and cross-platform virtual package injection.
  - `exporter.py` — Adapter over conda's exporter plugin registry.
    `render_envs` routes `Environment` objects through a named
    exporter. `available_formats` lists registered format names.
  - `cli.py` — `configure_parser` and `execute` for the conda
    plugin hook (`conda presto`), plus `main()` for the standalone
    `conda-presto` script entry point.
  - `plugin.py` — Conda plugin registration via `pluggy`. Lazily
    imports `cli` to keep `conda` startup fast.
  - `config.py` — All `CONDA_PRESTO_*` environment variable
    parsing. Module-level constants, no classes.
  - `exceptions.py` — `UnknownFormatError`, `SAFE_ERROR_TYPES`
    allow-list, and `safe_error_message` sanitizer.

- `action.yml` at the repo root is the composite GitHub Action.
  Supports `mode: local` (installs via pixi) and `mode: remote`
  (calls a hosted endpoint). Currently only `command: solve`.

- `tests/` mirrors the source: `test_app.py`, `test_resolve.py`,
  `test_exporter.py`, `test_cli.py`, `test_config.py`,
  `test_plugin.py`, `test_benchmarks.py`, plus `conftest.py` with
  shared fixtures.

- `docs/` uses Sphinx with conda-sphinx-theme, myst-parser,
  sphinx-design, and sphinxcontrib-mermaid. Follows the Diataxis
  framework: tutorials, reference, explanation, plus a proposals
  section for design documents.

## Imports

- Use `from __future__ import annotations` in all modules.
- Use relative imports for intra-package references
  (`from .config import ...`, `from .exceptions import ...`).
- Lazy imports are acceptable in `plugin.py` (loaded on every
  `conda` invocation) and `cli.py:cmd_serve` (uvicorn is optional).
  Everywhere else, imports belong at module top.

## Dependencies

- Minimize the dependency graph. Core resolving (`resolve.py`,
  `cli.py`, `exporter.py`) depends only on conda, conda-rattler-
  solver, and msgspec. The HTTP layer (`app.py`) adds litestar,
  anyio, and litestar-mcp. These are split into pixi features
  (`server` vs default).
- Pin minimum versions in `pyproject.toml` dependencies.

## Typing and linting

- All code must be typed using modern annotations (`str | None`,
  `list[str]`, `dict[str, str]`). Use `from __future__ import
  annotations` for forward references.
- Use `ruff` for linting and formatting. Configuration is in
  `pyproject.toml`.
- Use `msgspec.Struct` for data transfer objects that need fast
  serialization (API responses). Use `dataclasses.dataclass` for
  internal models that don't need wire serialization.

## Code structure

- Keep the flat module layout. New features should extend existing
  modules or add a new module at `conda_presto/<feature>.py`. Do
  not create subpackages unless the feature has 3+ modules of its
  own.
- Route handlers in `app.py` should be thin: validate input, call
  a helper, return a response. Business logic belongs in the
  feature module.
- Each route handler should include MCP metadata kwargs
  (`mcp_tool`, `mcp_description`, `mcp_when_to_use`, `mcp_returns`,
  `mcp_agent_instructions`) so the MCP endpoint auto-discovers it.
- Error responses use `Response({"error": ...}, status_code=...)`.
  Never leak internal paths or stack traces to clients.
- Configuration goes through `config.py` as `CONDA_PRESTO_*`
  environment variables with sensible defaults.

## Testing

- Tests are plain `pytest` functions. No `unittest.TestCase` or
  class-based grouping.
- Never use `unittest.mock`. Use `monkeypatch.setattr` with
  recording closures for observing calls.
- HTTP endpoint tests use `httpx.AsyncClient` with
  `ASGITransport(app=test_app)`. The `test_app` fixture creates a
  minimal Litestar app without middleware (no rate limiting, no
  compression) for fast, isolated tests.
- Use `@pytest.mark.anyio` for async tests.
- Use `@pytest.mark.parametrize` with `ids=[...]` for readable
  output. Consolidate cases that exercise the same logic.
- Shared fixtures go in `conftest.py`.
- After changes, run `pixi run -e test pytest -m "not crossplatform"`
  and `pixi run -e dev lint` to verify.

## Performance conventions

- `msgspec.Struct` for API response models (`ResolvedPackage`,
  `SolveResult`). Litestar encodes them natively without dict
  conversion.
- Solver calls run off the event loop via `anyio.to_thread` with a
  capacity limiter. Multi-platform solves use `ProcessPoolExecutor`.
- `RattlerIndexHelper` instances are cached in-memory keyed by
  `(channels, platform)`. Building an index (~700 ms) is the
  dominant cost; cache reduces repeat solves to SAT time (~20-100 ms).

## Security conventions

- File content from clients is written to a temp file with a
  whitelisted extension. Directory components are stripped from
  filenames. No path traversal.
- Only `SAFE_ERROR_TYPES` exceptions surface their message to
  clients. Everything else returns a generic "Internal solver error"
  with full detail logged server-side.
- Rate limiting, request size limits, and per-request caps
  (max specs, max platforms, solve timeout) are all configurable.

## Lockfile maintenance

- After any change to `pyproject.toml` that affects pixi metadata
  (dependencies, features, tasks), run `pixi lock` and commit the
  updated `pixi.lock` alongside the change.

## Pull request conventions

- One feature per PR. Draft PRs for work in progress.
- PR bodies use one line per paragraph or bullet (GitHub wraps in
  the browser). No hard-wrapping at 72 columns.
- Commit messages: imperative present tense, subject under 72 chars.
- News/changelog entries go in `docs/changelog.md` under the
  `(unreleased)` section.

## Proposal-driven development

- Design proposals live in `docs/proposals/` organized by stream:
  `capability/`, `integration/`, `trust/`.
- Each proposal specifies API surface, implementation outline, test
  strategy, open questions, and effort estimate.
- Implementation should stay close to the proposal. Deviations are
  fine when justified, but note them in the PR description.
- Keep a changelog at the bottom of each proposal document. Record
  the date, what changed, and why. This makes design evolution
  traceable without digging through git history.
