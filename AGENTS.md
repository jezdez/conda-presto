# AGENTS.md - conda-presto coding guidelines

## Project Structure

- `conda-presto` exposes the same solver through a conda subcommand, a standalone CLI, an HTTP API, and a GitHub Action.

- Core package modules live under `conda_presto/`:
  - `cli.py` owns parser setup, conda subcommand dispatch, and standalone CLI execution.
  - `app.py` owns Litestar route handlers and HTTP request/response behavior.
  - `resolve.py` owns solver/index/cache interactions and cross-platform solve execution.
  - `inputs.py` owns input-file parsing through conda's environment specifier plugin registry.
  - `exporter.py` owns output-format rendering through conda's exporter plugin registry.
  - `config.py`, `exceptions.py`, and `plugin.py` keep configuration, safe error surfaces, and conda plugin registration separate.

- Tests live in `tests/` and mirror the module or behavior under test: `tests/test_app.py` for HTTP handlers, `tests/test_cli.py` for CLI behavior, `tests/test_resolve.py` for solver internals, `tests/test_exporter.py` for output formats, and `tests/test_plugin.py` for conda plugin registration. Cross-cutting fixtures belong in `tests/conftest.py`.

- Documentation uses Sphinx with MyST, `conda-sphinx-theme`, `sphinx-design`, `sphinx-copybutton`, and `sphinxcontrib-mermaid`. Keep docs source under `docs/`; generated `docs/_build/` output is not source.

## Imports

- Use relative imports for intra-package references, e.g. `from .resolve import solve`. Absolute `conda_presto.*` imports should appear in tests, entry points, or code that must import through the installed package surface.

- Inline imports are reserved for optional or expensive dependencies and startup-sensitive plugin paths. Acceptable cases include lazy `uvicorn` import in `cmd_serve()` and local imports that avoid loading server-only dependencies during conda plugin discovery. Everywhere else, imports belong at module top.

- Use `from __future__ import annotations` in Python modules.

## Dependencies

- Minimize dependencies. Prefer stdlib, conda APIs, or already-required packages before adding a new dependency.

- Prefer conda's public APIs and plugin registries over reimplementing platform detection, environment parsing, exporter lookup, or solver configuration.

- Pin minimum supported versions in `pyproject.toml`, not exact versions, unless a temporary upper bound is needed for a known incompatibility.

- After any change to Pixi metadata in `pyproject.toml` (dependencies, features, tasks, environments, workspace settings), run `pixi lock` and commit `pixi.lock` with the metadata change.

## Code Structure

- Prefer methods on existing classes over module-level private helpers. If a helper is parameterized by data already held by a class, put the behavior on that class. Call sites should read like `ParsedInputFile.from_content(...)` or `OutputFormat.named(...).render(...)`, not `_parse_input(data, filename, target_platforms)`.

- Before adding a new private module-level helper, check in order:
  1. Does conda or an existing dependency already expose this behavior?
  2. Does an existing dataclass or plugin adapter own the data?
  3. Is the helper genuinely reused across modules and worth making public with a short docstring?
  4. If it is called once, inline it.

- Do not add section-divider comments such as `# --- Helpers ---` or `# === Public API ===`. Use ordering and module boundaries. If a file wants section headers, split the file or move behavior to the owning class.

- Comments should explain intent, constraints, or trade-offs that code cannot express. Do not narrate obvious operations.

- Docstrings should be short and useful. Do not repeat types from annotations.

## CLI and HTTP Contracts

- Keep CLI and HTTP behavior aligned where they expose the same operation. Output-format support should go through conda's exporter registry, and input-file support should go through conda's environment specifier registry.

- `--format` and `?format=` select output formats. The default CLI/HTTP JSON shape is conda-presto's native `SolveResult` list, not an exporter plugin.

- Solver failures on native JSON paths should stay representable per platform where possible. Exporter paths operate on successful `Environment` objects and may return an HTTP 500 or CLI failure when solving fails.

- Be careful with conda global context. Cross-platform operations must configure the intended platform and virtual package overrides deliberately; do not rely on host defaults leaking through.

## Testing

- Tests are plain pytest functions. Do not use `unittest.TestCase` or test classes.

- Prefer pytest fixtures, `monkeypatch`, `capsys`, `tmp_path`, and small local fakes over `unittest.mock`, `Mock`, or `MagicMock`.

- Use `pytest.mark.parametrize` when multiple cases exercise the same logic with different inputs. Check whether a new test can be a parameter on an existing test before adding a standalone function. Use readable `id=` values.

- Put repeated setup in fixtures rather than repeated inline blocks. Fixtures that return recording closures or call logs are preferred for observing calls.

- Keep tests focused and named as behavior specifications. Avoid long test docstrings; long prose drifts from the code.

- For subprocess tests, include stderr/stdout in assertion messages when a bare exit-code assertion would be hard to diagnose in CI.

- After modifying production code or tests, run the relevant local checks before considering the work done:
  - `pixi run lint`
  - `pixi run format --check` when formatting may have changed
  - `pixi run -e test test`

## Documentation

- Follow Diataxis: tutorials teach workflows, reference describes exact interfaces, explanation covers architecture/trade-offs, and proposal/roadmap pages track planned work.

- Keep prose direct. Avoid excessive bold/italic emphasis and avoid hard-wrapping text in GitHub-facing issue or PR bodies.

- Prefer real docs pages and concise roadmap links over long proposal corpora in the repository. Detailed future design discussion belongs in GitHub issues once it is not active implementation documentation.

- When adding an endpoint, CLI flag, output format, or environment variable, update the relevant reference page and one tutorial/example when user workflow changes.

## Changelog

- `CHANGELOG.md` is the canonical changelog. `docs/changelog.md` should include it rather than duplicate release notes.

- Put unreleased user-visible changes under `[Unreleased]`. Do not add new work under an already released version.

- Use concise bullets in imperative or descriptive present tense matching the surrounding changelog style.

## Pull Requests and Issues

- Use the repository's native title and body style. Never prefix PR titles with tool-specific tags.

- Follow issue and pull request templates when they exist.

- Do not put validation steps, test commands, or verification output in PR descriptions.

- GitHub renders Markdown with GFM. Write PR and issue bodies as one line per paragraph or bullet; let GitHub wrap in the browser. Reserve hard wraps for code fences, tables, and CLI help text.

- When using `gh pr create` or `gh issue create`, use a heredoc for multi-line bodies and keep each bullet on one physical line.

## GitHub and CI

- Use `gh` for GitHub issues, pull requests, checks, and Actions logs when local repository context is relevant.

- For failing GitHub Actions checks, inspect the specific failing job log before changing code. Distinguish root-cause failures from downstream cleanup/output failures.

- Keep workflow tests close to the workflow or action they verify. Composite action behavior belongs in a dedicated workflow smoke test plus lightweight local tests for script/string generation where practical.

