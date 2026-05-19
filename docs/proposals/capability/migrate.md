# migrate: pip-to-conda environment migration

Status: implementation in progress
Owner: Rohan K
Filed: 2026-05-06
Depends on: nothing. Future: [preflight](preflight.md) (faster availability
checks), [why-not](why-not.md) (structured conflict data).
Implementation order: independent of the trust track. Ships as CLI
flag (`--migrate`) and HTTP endpoint (`POST /migrate`).

## TL;DR

Accepts a pip spec file (requirements.txt, pyproject.toml, Pipfile,
poetry.lock), translates package names to conda equivalents, checks
per-package availability on configured channels, classifies unavailable
packages by reason, and validates the result via dry-run solve.

```
POST /migrate
  Content-Type: application/json
  {
    "file": "numpy>=1.24\npandas>=2.0\nouterbounds>=0.3\n",
    "channels": ["conda-forge"],
    "platform": "linux-64"
  }
  → 200 OK
  {
    "platform": "linux-64",
    "source_format": "requirements",
    "packages": [
      {"pip_name": "numpy", "conda_name": "numpy",
       "pip_version_spec": ">=1.24", "conda_spec": "numpy >=1.24",
       "status": "available", "reason": null},
      {"pip_name": "pandas", "conda_name": "pandas",
       "pip_version_spec": ">=2.0", "conda_spec": "pandas >=2.0",
       "status": "available", "reason": null},
      {"pip_name": "outerbounds", "conda_name": "outerbounds",
       "pip_version_spec": ">=0.3", "conda_spec": "outerbounds >=0.3",
       "status": "unavailable", "reason": "not_in_conda"}
    ],
    "solve_success": true,
    "solve_error": null
  }
```

## Motivation

Enterprise Anaconda customers migrating from pip need to know:

1. What percentage of their pip stack is available as conda packages
2. Which packages aren't available and why (pip-only, version gap, wrong arch)
3. Whether the available subset solves cleanly together

No existing tool answers all three. `conda create --dry-run` can't
parse pip formats or translate names. Grayskull focuses on recipe
generation, not bulk migration assessment.

## Why this belongs in conda-presto

conda-presto provides the hard infrastructure: fast solving via
rattler (~15ms cached), repodata caching, HTTP API, MCP exposure.
Migration adds only the pip→conda boundary crossing logic — parsing
pip formats, translating names, and classifying availability.

## Architecture

```
conda_presto/
  migrate/            # subpackage (4 modules, meets AGENTS.md threshold)
    __init__.py       # migrate() orchestration: parse → map → solve → classify
    parsers.py        # requirements.txt, pyproject.toml, Pipfile, poetry.lock
    name_mapping.py   # pip→conda name translation (static + normalization)
    models.py         # MappedPackage, MigrationResult (msgspec Structs)
```

Changes to existing files (additive only):
- `cli.py`: `--migrate` flag, `cmd_migrate()`, `_print_migrate_result()`
- `app.py`: `POST /migrate` route handler, registered in `route_handlers`

## Resolution flow

1. **Parse** — detect format, extract `{name: version_spec}` pairs
2. **Map** — translate pip names to conda (static table + normalization)
3. **Bulk solve** — try all packages at once (fast path)
4. **Classify** — if bulk fails, probe each package individually:
   - Available: included in re-solve
   - Unavailable: classify reason (version_not_available, wrong_arch, not_in_conda)
5. **Re-solve** — solve available packages together
6. **Report** — `MigrationResult` with per-package status

## CLI surface

```
conda presto --migrate -f requirements.txt -p linux-64
conda presto --migrate -f pyproject.toml -c conda-forge --json
```

Human-readable output by default; `--json` for MigrationResult JSON.

## HTTP surface

`POST /migrate` with JSON body or raw text/plain (same Content-Type
dispatch pattern as `/resolve`).

## Unavailability reasons

When a package is unavailable, graduated probes classify why:

1. Drop version constraint, solve bare name → "version_not_available"
2. Solve bare name on other platforms → "wrong_arch"
3. All fail → "not_in_conda" (truly pip-only)

## Name translation

- Primary: cf-graph-countyfair mapping (~12k entries, fetched once per session)
- Static fallback table for known divergences (opencv-python→opencv, etc.)
- Normalization: lowercase, strip extras, underscore→hyphen

## Relationship to conda-pypi

conda 26.5 introduced the `conda-pypi` plugin for unified conda+PyPI resolution.
Our tool is complementary:

- **conda-pypi** handles install-time resolution (single solve across conda + wheels)
- **migrate** handles pre-migration analysis (what goes where, what's coverage, is conda section solvable) and produces the environment.yml that conda consumes

Current state (2026-05-19):
- `conda env create -f environment.yml` does NOT use conda-pypi yet (draft PR conda-pypi#349)
- Only pure Python wheels supported via conda-pypi channel (compiled packages must come from conda channels)
- conda-pypi issue #348 proposes `conda pypi migrate-env` — same concept as this feature

Our `pip:` section output works on any conda 26.5+ regardless of conda-pypi config.
When conda-pypi env file integration ships, a `--unified` output mode could produce
a flat dependency list for users with the unified solver configured.

## What this does NOT do

- PSM policy detection (belongs in [admit](../trust/admit.md))
- Runtime import validation (out of scope)
- Unified conda-pypi resolution (handled by conda-pypi at install time)

## Test strategy

48 tests covering:
- Format detection and parsing (requirements.txt, pyproject.toml)
- Name translation (cf-graph mapping, static fallback, normalization, network failure)
- Bulk solve happy path (data science stack)
- Mixed availability (conda-available + pip-only)
- Cross-platform solving (linux-64 from macOS)
- Edge cases (empty input, all unavailable, compound version specs)
- Unavailability classification (version gap, wrong arch, pip-only)
- environment.yml output (channels, pip section ordering, valid YAML)
- CLI flags (`--migrate`, `--json`, `-p`, multi-file `---` separator)
- HTTP endpoint (JSON body, text/plain, errors)

All tests hit real conda-forge repodata (~48s full suite).

## Future integration points

| When this ships... | What migrate gains |
|--------------------|-------------------|
| [preflight](preflight.md) | Replace per-package solver probes with fast index lookup |
| [why-not](why-not.md) | Structured conflict chains on solve failure |
| [lint](lint.md) | Lint generated environment.yml output |
| [admit](../trust/admit.md) | Policy-aware explanations for filtered packages |
| conda-pypi env integration (#349) | `--unified` output mode (flat deps, no pip section) |
| conda-presto /transcode | `--lockfile` output mode (conda-lock.yaml) |

## Effort

- Core logic + tests: done (~3 days)
- CLI + HTTP endpoint: done (~1 day)
- cf-graph mapping + renderer + CLI output rework: done (~1 day)
- Total: ~5 days, single PR

## Changelog

- 2026-05-19: Added cf-graph name mapping, environment.yml renderer,
  channels section, multi-file `---` separators, detailed stderr summary.
  Documented relationship to conda-pypi and conda 26.5 features.
  48 tests passing.
- 2026-05-18: Restructured CLI from positional subcommand to `--migrate` flag.
- 2026-05-08: Core logic, CLI, HTTP endpoint, unavailability classification
  all implemented and tested (33 tests passing).
- 2026-05-06: Initial implementation of core migration logic.
