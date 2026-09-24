# Demo recordings

Terminal demonstrations recorded with [VHS](https://github.com/charmbracelet/vhs). Each recording types the commands for one workflow, with its setup kept out of view. GIFs appear directly in the relevant documentation pages. PNG previews provide a still image for readers who prefer reduced motion.

## User workflows

| Demo | Workflow |
|---|---|
| `cli` | [Resolve requirements and export declarations](../docs/how-to/resolve-from-cli.md) |
| `workspace` | [Discover, solve and maintain a workspace](../docs/tutorials/workspaces.md) |
| `locks` | [Extract saved records and generate SBOMs](../docs/how-to/extract-workspace-lock.md) |

## Services

| Demo | Workflow |
|---|---|
| `http` | [Resolve through HTTP and retrieve retained bytes](../docs/tutorials/http-api.md) |
| `cache` | [Retain results across a service restart](../docs/how-to/configure-result-cache.md) |
| `docker` | [Run the server image](../docs/how-to/run-with-docker.md) |
| `operations` | [Inspect readiness, capabilities and local timings](../docs/how-to/monitor-service.md) |

## Integrations

| Demo | Workflow |
|---|---|
| `trust` | [Verify a public signed fixture offline](../docs/how-to/sign-and-verify.md) |
| `action` | [Use the GitHub Action client](../docs/how-to/use-github-action.md) |

The trust demo uses the [public Sigstore fixture](../tests/fixtures/sigstore/README.md). It verifies existing signatures without acquiring credentials or signing an artifact. The Action demo executes the real composite Action client locally. Hosted runner execution is checked separately by the Action smoke workflow.

## Run the examples

From the repository root:

```bash
pixi run --locked -e examples examples-check
pixi run --locked -e examples bash demos/check.sh workspace locks
pixi run --locked -e examples bash demos/check.sh docker
```

The default check runs all examples except Docker. Each script checks actual results and cleans up its temporary files and local services. Run the scripts from this checkout so their fixtures and helpers are available. Solves require channel access. The workspace examples require the unreleased source stack.

## Record demos

```bash
# Record every demo
pixi run --locked -e demos demos

# Record selected demos
pixi run --locked -e demos demos cli workspace
```

The locked Pixi environment provides VHS, ttyd, FFmpeg and `bat`. VHS locates or downloads a Chromium browser. The shared settings use JetBrains Mono, Dark+ and a green prompt, matching the conda-workspaces recordings. Install JetBrains Mono locally when recording if it is not already available.

The Docker demo requires a running Docker daemon and network access for the image build and solve. It leaves the `conda-presto:docs-demo` image available locally. Its recording omits the build wait.

Review regenerated GIFs, PNG previews and text transcripts before committing them. Recordings and runnable scripts cover the same workflows. Transcripts come from complete checked example runs, with machine-specific paths replaced by placeholders. Package versions, identifiers and timings can change between runs. CI checks the runnable examples and builds documentation from committed media.

## File structure

- `_settings.tape` contains the shared terminal appearance and timing defaults.
- `*.tape` contains the recorded commands and hidden setup for each workflow.
- `*.sh` and workflow directories contain runnable examples, fixtures and result checks.
- `*.gif` contains the animated recordings embedded in documentation.
- `*.png` contains still previews for reduced motion.
- `*.txt` contains downloadable text transcripts.
