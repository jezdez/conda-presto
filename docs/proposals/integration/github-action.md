# github-action: `jezdez/conda-presto@v0.4.0` — first-party GitHub Action

Status: implemented (solve, transcode, lint commands)
Owner: TBD
Filed: 2026-04-16
Depends on: [transcoder](../capability/transcoder.md), [diff](../capability/diff.md) — soft dependencies,
each makes the action more useful

## TL;DR

Composite GitHub Action that lives in the conda-presto repo itself
(`action.yml` at the root). Supports two modes:

- **local** (default): installs conda-presto on the runner via pixi
  and runs the CLI directly. No infrastructure required.
- **remote**: calls a hosted conda-presto HTTP API endpoint.

Supports `command: solve`, `command: transcode`, and `command: lint`.
Future commands (`diff`, `preflight`) will be added as those
endpoints land.

## Motivation

- **Distribution lever.** People discover tools via CI templates.
  A high-quality Action moves adoption faster than docs or blog posts.
- **Composes with everything else.** Every endpoint becomes a
  one-line CI step. `/diff` + auto-PR-comment is the killer demo.
- **Low risk, high reach.** It's a `.yml` + a small entrypoint
  script, co-located with the main repo so API and action evolve
  in lockstep.
- **Two deployment shapes.** Local mode works out of the box for
  anyone. Remote mode is faster for teams that already run a
  conda-presto deployment (shared repodata cache, no install step).

## API surface

```yaml
# Solve an environment file
- uses: jezdez/conda-presto@v0.5.0
  with:
    command: solve
    file: environment.yml
    platforms: linux-64,osx-arm64

# Transcode a lockfile (remote mode)
- uses: jezdez/conda-presto@v0.5.0
  with:
    mode: remote
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
    command: transcode
    file: pixi.lock
    format: conda-lock-v1
    output: conda-lock.yml

# Lint an environment file
- uses: jezdez/conda-presto@v0.5.0
  with:
    mode: remote
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
    command: lint
    file: environment.yml
    severity: warning
```

Inputs:

- `mode`: `local` (default) or `remote`
- `command`: `solve`, `transcode`, or `lint` (required)
- `file`: path to input file
- `specs`: comma-separated specs (solve only)
- `channels`: comma-separated channels (default: `conda-forge`)
- `platforms`: comma-separated target platforms (solve/transcode)
- `format`: output format name (solve/transcode)
- `output`: path to write the response body to (default stdout)
- `endpoint`: conda-presto base URL (required when `mode: remote`)
- `ignore`: comma-separated lint rule codes to skip (lint only)
- `severity`: minimum severity filter for lint (lint only)

Outputs:

- `result`: the response body
- `solved`: `true|false`

## Implementation outline

1. `action.yml` at the repo root (composite, `using: composite`).
2. **Local path** (3 steps):
   - Install pixi via `prefix-dev/setup-pixi` (pinned to commit hash).
   - `pixi global install --git` to install conda-presto.
   - Build CLI args from inputs and run `conda-presto`.
3. **Remote path** (1 step):
   - Validate `endpoint` is set, build JSON body from inputs,
     POST to `{endpoint}/resolve`.
4. **Shared output step**: reads the temp file from whichever mode
   ran, checks for per-platform solver errors, writes `solved` and
   `result` to `$GITHUB_OUTPUT`, optionally writes to `output` path.
5. Security: action passes zizmor with zero findings (pinned hashes,
   no template injection, inputs passed via env vars).

## Tests

- Smoke tests in CI: run `command: solve` in local mode against a
  small fixture, check expected output.
- Integration test: a workflow that runs `diff` on a PR with an
  intentional dep bump and asserts the comment posts (after diff
  endpoint lands).

## Effort

Done: ~½ day for the dual-mode action with solve support.
Remaining: ~1 hour per additional command as endpoints land.

## Out of scope

- Action for non-GitHub CI (GitLab, CircleCI, Buildkite). Each
  needs its own native packaging; cross that bridge when asked.
- Marketplace listing & icon work — polish, do once at v1.0.0.
- `comment-on-pr` input for diff (deferred until `/diff` endpoint).

## Related

- conda-meta-mcp's existing action: `conda-incubator/conda-meta-mcp@main`
  — same composite pattern, good reference for layout and CI.

## Changelog

- 2026-05-08: Expanded action.yml with `command: transcode` and
  `command: lint`. Added `ignore` and `severity` inputs for lint.
  Remote mode routes to the correct endpoint per command. Local lint
  deferred (no CLI subcommand yet). Updated output handling for
  lint findings.
- 2026-04-16: Initial implementation with `command: solve` only.
