# Demos

Six recordings show the public CLI and HTTP workflows. Each appears beside its instructions, with a static preview for reduced motion and a transcript from a complete checked example run. Package versions, identifiers and timings depend on the recording environment.

## CLI

| Workflow | What it shows |
|---|---|
| {ref}`demo-cli` | Solve for multiple platforms, merge files and inline requirements, and export exact URLs or declarations with `conda presto`. |
| {ref}`demo-workspace` | Discover named targets, solve a workspace, validate its lock and update one target. |
| {ref}`demo-locks` | Extract saved records, convert a generic lock and generate an SBOM with declared roots. |

## HTTP

| Workflow | What it shows |
|---|---|
| {ref}`demo-http` | Resolve inline requirements or an uploaded environment YAML file, then retrieve exact retained bytes. |
| {ref}`demo-http-workspace` | Discover and select workspace targets, export declarations and solve the complete matrix. |
| {ref}`demo-http-update` | Validate a saved workspace lock, reject a changed manifest and update one target. |

The workspace features require the source checkout described in {doc}`../tutorials/workspaces`. Solving requires public channel access.

## Run or record an example

From the repository root:

```bash
pixi run --locked -e examples examples-check
pixi run --locked -e demos demos workspace
pixi run --locked -e demos demos http http-workspace http-update
```

The first command checks all runnable examples except Docker. The other commands record selected workflows. Omit the names to record all six.

The locked recording environment supplies VHS, ttyd, FFmpeg and `bat`. VHS locates or downloads a Chromium browser. The HTTP examples start local services and clean them up on exit. Recording does not require Docker.

Shared settings, tapes, fixtures, recordings and transcripts live in `demos/`. See its {download}`recording guide <../../demos/README.md>` for prerequisites and the additional runnable operator examples. CI checks the examples and builds documentation from committed media.
