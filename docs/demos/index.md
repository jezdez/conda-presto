# Demos

Six recordings show the public CLI and HTTP workflows. Each appears beside its written instructions, with a static preview for reduced motion. Package versions, identifiers and timings depend on the recording environment.

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

## Record a demo

From the repository root:

```bash
pixi run --locked -e demos demos workspace
pixi run --locked -e demos demos http http-workspace http-update
```

These commands record selected workflows. Omit the names to record all six. Each VHS tape contains its workflow commands and checks, with temporary directories, fixtures and local services prepared by shared setup.

The locked recording environment supplies VHS, ttyd, FFmpeg and `bat`. VHS locates or downloads a Chromium browser. The HTTP examples start local services and clean them up on exit. Recording does not require Docker.

Shared settings, tapes, fixtures and recordings live in `demos/`. See its {download}`recording guide <../../demos/README.md>` for prerequisites. Documentation CI builds committed media. Product and GitHub Action tests run in their existing CI workflows.
