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

## Run the examples

From the repository root:

```bash
pixi run --locked -e examples examples-check
pixi run --locked -e examples bash demos/check.sh workspace locks
pixi run --locked -e examples bash demos/check.sh http http-workspace http-update
```

The default check runs all examples except Docker. Each script checks actual results and cleans up its temporary files and local services. Run the scripts from this checkout so their fixtures and helpers are available. Solves require channel access. The workspace examples require the unreleased source stack.

## Record demos

```bash
# Record all six CLI and HTTP demos
pixi run --locked -e demos demos

# Record selected demos
pixi run --locked -e demos demos cli http-workspace
```

The locked Pixi environment provides VHS, ttyd, FFmpeg and `bat`. VHS locates or downloads a Chromium browser. Shared settings use JetBrains Mono, Dark+ and a green prompt. Install JetBrains Mono locally if it is not already available. Recording does not require Docker or signing credentials.

Review regenerated GIFs, PNG previews and text transcripts before committing them. Recordings and runnable scripts cover the same workflows. Transcripts come from complete checked example runs, with machine-specific paths replaced by placeholders. Package versions, identifiers and timings can change between runs. CI checks the runnable examples and builds documentation from committed media.

## Additional runnable examples

These scripts remain checked examples for deployment and integration recipes. They do not have GIF recordings.

| Example | Workflow |
|---|---|
| `cache` | [Retain results across a service restart](../docs/how-to/configure-result-cache.md) |
| `docker` | [Build and run the server image](../docs/how-to/run-with-docker.md) |
| `operations` | [Inspect readiness, capabilities and local timings](../docs/how-to/monitor-service.md) |
| `trust` | [Verify a public signed fixture offline](../docs/how-to/sign-and-verify.md) |
| `action` | [Run the GitHub Action client locally](../docs/how-to/use-github-action.md) |

The trust example uses the [public Sigstore fixture](../tests/fixtures/sigstore/README.md) without acquiring credentials or signing an artifact. The Action example executes the real composite Action client locally. Hosted runner execution is checked separately by the Action smoke workflow.

The Docker example requires a running Docker daemon and network access for the image build and solve. Run it with `pixi run --locked -e examples bash demos/check.sh docker`. It leaves the `conda-presto:docs-demo` image available locally.

## File structure

- `_settings.tape` contains the shared terminal appearance and timing defaults.
- `*.tape` contains the recorded commands and hidden setup for the six workflows.
- `*.sh` and workflow directories contain runnable examples, fixtures and result checks.
- `*.gif` contains the animated recordings embedded in documentation.
- `*.png` contains still previews for reduced motion.
- `*.txt` contains downloadable transcripts of the recorded workflows' checked examples.
