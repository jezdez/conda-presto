# github-action: `actions/conda-presto@v1` — first-party GitHub Action

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: [transcoder](01-transcoder.md), [diff](11-diff.md) — soft dependencies,
each makes the action more useful

## TL;DR

Composite GitHub Action that wraps the conda-presto HTTP API for CI
use cases: `solve`, `transcode`, `diff`, `preflight`. Configurable to
point at a self-hosted instance or the public deployment. Distributed
under `jezdez/conda-presto-action@v1` (or under the
`conda-incubator` org if/when conda-presto moves there).

## Motivation

- **Distribution lever.** People discover tools via CI templates.
  A high-quality Action moves adoption faster than docs or blog posts.
- **Composes with everything else.** Every endpoint becomes a
  one-line CI step. `/diff` + auto-PR-comment is the killer demo.
- **Low risk, high reach.** It's a `.yml` + a small entrypoint
  script. No new code in conda-presto itself.
- **Pairs naturally with conda-meta-mcp's existing action**
  (`conda-incubator/conda-meta-mcp@main`) — both projects ship a
  composite Action, both follow the same pattern, agents can use
  both.

## API surface

```yaml
# Solve a manifest, fail the job if it doesn't solve cleanly
- uses: jezdez/conda-presto-action@v1
  with:
    command: solve
    file: environment.yml
    platforms: linux-64,osx-arm64

# Transcode pixi.lock → conda-lock.yml
- uses: jezdez/conda-presto-action@v1
  with:
    command: transcode
    file: pixi.lock
    format: conda-lock-v1
    output: conda-lock.yml

# Diff the head against the base, post as PR comment
- uses: jezdez/conda-presto-action@v1
  with:
    command: diff
    base: ${{ github.event.pull_request.base.sha }}/environment.yml
    head: environment.yml
    comment-on-pr: true

# Validate without solving (fast pre-commit-style check)
- uses: jezdez/conda-presto-action@v1
  with:
    command: preflight
    file: environment.yml
```

Inputs:

- `command`: `solve | transcode | diff | preflight` (required)
- `file`: path to input file (mutually exclusive with `specs`)
- `specs`: comma-separated specs
- `platforms`: comma-separated; defaults to native
- `format`: output format name
- `output`: path to write the response body to (default stdout)
- `endpoint`: conda-presto base URL (default: public deployment)
- `comment-on-pr`: post the result as a PR comment (diff command)

Outputs:

- `result`: the response body
- `result-path`: path written to (when `output` is set)
- `solved`: `true|false` for solve / preflight
- `diff-summary`: short text summary for diff (`+12 -3 ~4`)

## Implementation outline

1. Repo: `jezdez/conda-presto-action` (new repo).
2. `action.yml` declares inputs/outputs and runs a small Python or
   bash entrypoint that:
   - Reads inputs from env (`INPUT_*`)
   - Loads file content if `file` is set
   - POSTs to `{endpoint}/{command}` with the right body
   - Writes outputs back to `$GITHUB_OUTPUT`
   - Posts a PR comment via `gh api` for `comment-on-pr`
3. Use composite `using: composite` rather than Docker — fast cold
   start, no image to maintain.
4. CI: a small matrix that exercises each command against the
   public deployment.
5. Tag releases: `v1`, `v1.0.0`, etc., following the conda-meta-mcp
   action's pattern.

## Tests

- Action unit tests: bash entrypoint is testable with `bats` or just
  pytest + subprocess. Cover each command's argument plumbing.
- Smoke tests in CI: run each command against a small fixture, check
  expected output.
- Integration test: a workflow that runs `diff` on a PR with an
  intentional dep bump and asserts the comment posts.

## Effort

~½ day for the Action itself.
~½ day for solid test coverage and a polished README.

Best timed to ship right after [diff](11-diff.md) (`/diff`) lands so the most
compelling use case is available out of the box.

## Out of scope

- Action that runs conda-presto *server-side* in CI (i.e. spinning
  up the service inside the GH runner instead of calling a hosted
  one). Possible later if people want fully air-gapped CI; defer.
- Action for non-GitHub CI (GitLab, CircleCI, Buildkite). Each
  needs its own native packaging; cross that bridge when asked.
- Marketplace listing & icon work — polish, do once at v1.0.0.

## Related

- conda-meta-mcp's existing action: `conda-incubator/conda-meta-mcp@main`
  — same composite pattern, good reference for layout and CI.
