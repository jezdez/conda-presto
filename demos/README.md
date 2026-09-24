# Demo recordings

Six terminal demonstrations recorded with [VHS](https://github.com/charmbracelet/vhs) cover the public CLI and HTTP interfaces. Each recording types the workflow commands with setup kept out of view. GIFs appear beside the documentation instructions, with PNG previews for readers who prefer reduced motion.

## CLI

| Demo | Workflow |
|---|---|
| `cli` | [Solve for multiple platforms, merge inputs and export exact URLs or declarations](../docs/how-to/resolve-from-cli.md) |
| `workspace` | [Discover, solve and maintain a workspace](../docs/tutorials/workspaces.md) |
| `locks` | [Extract saved records and generate SBOMs](../docs/how-to/extract-workspace-lock.md) |

The CLI examples use `conda presto`.

## HTTP

| Demo | Workflow |
|---|---|
| `http` | [Resolve inline requirements and uploaded YAML, then retrieve retained bytes](../docs/tutorials/http-api.md) |
| `http-workspace` | [Discover targets, export declarations and solve a workspace](../docs/tutorials/http-api.md#discover-and-solve-a-workspace) |
| `http-update` | [Validate and update a saved workspace lock](../docs/tutorials/http-api.md#validate-a-saved-workspace-lock) |

## Record demos

From the repository root:

```bash
# Record all six CLI and HTTP demos
pixi run --locked -e demos demos

# Record selected demos
pixi run --locked -e demos demos cli http-workspace
```

The locked Pixi environment provides VHS, ttyd, FFmpeg and `bat`. VHS locates or downloads a Chromium browser. Shared settings use JetBrains Mono, Dark+ and a green prompt. Install JetBrains Mono locally if it is not already available. Solves require channel access, and the workspace recordings require the unreleased source stack. Recording does not require Docker or signing credentials.

Each tape owns its workflow commands and checks. Shared `_setup.sh` prepares a temporary working directory, fixtures and a local HTTP service when needed, then cleans up on exit. `record.py` invokes VHS for the requested tapes.

Review regenerated GIFs and PNG previews before committing them. Package versions, identifiers and timings can change between runs. Documentation CI builds committed media. Product and GitHub Action tests run in their existing CI workflows.

## Deployment and integration recipes

The documentation also covers [result caching](../docs/how-to/configure-result-cache.md), [Docker deployment](../docs/how-to/run-with-docker.md), [service monitoring](../docs/how-to/monitor-service.md), [signing and verification](../docs/how-to/sign-and-verify.md), and [GitHub Actions](../docs/how-to/use-github-action.md). The [Action workflow](action/workflow.yml) and [manifest](action/conda.toml) remain available as a hosted example. The Action smoke test uses that same manifest.

## File structure

- `_settings.tape` contains the shared terminal appearance and timing defaults.
- `*.tape` contains the recorded commands and checks for the six workflows.
- `_setup.sh` prepares temporary files and local services for the tapes.
- `record.py` invokes VHS for all or selected tapes.
- `workspace/` and `action/` contain shared input fixtures and the hosted Action example.
- `*.gif` contains the animated recordings embedded in documentation.
- `*.png` contains still previews for reduced motion.
