# Demos

These recordings execute the same scripts that CI checks. They cover the major user and operator workflows. Package versions, generated identifiers and timings depend on the recording environment. Each video has controls and a text transcript. Recordings replace machine-specific checkout and Python environment paths with placeholders.

The workspace features use the unreleased PR stack. Run these examples from its source checkout, as described in the {doc}`../tutorials/workspaces` tutorial. Public channel access is required for solving. The trust demo verifies a checked-in fixture offline. Docker is required only for the container demo.

## Run or record an example

```bash
pixi run --locked -e examples examples-check
pixi run --locked -e examples bash examples/demos/check.sh docker
pixi run --locked -e demos demos workspace
```

The first command runs all examples except Docker. Omit the demo name from the recording command to record all nine, including Docker. Recordings require a Chromium browser, which VHS locates or downloads, and a running Docker daemon for the container demo. The Pixi environment supplies VHS, ttyd and FFmpeg.

Scripts use temporary files, start their own services and clean them up on exit. The Docker example leaves the locally built `conda-presto:docs-demo` image available for reuse. Do not point the scripts at a production service.

VHS tapes live under `docs/demos/tapes/`. The [VHS command reference](https://github.com/charmbracelet/vhs#vhs-command-reference) describes their format. Recordings, posters and transcripts live under `docs/_static/demos/`. CI regenerates them before publishing a strict documentation build. To refresh the checked-in recordings, run `pixi run --locked -e demos demos` and review the resulting media.

(demo-cli)=
## CLI and declaration export

Resolve inline requirements with the standalone CLI, solve an environment file through the conda subcommand and render dependency declarations. See {doc}`../how-to/resolve-from-cli`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/cli.png" aria-label="CLI and declaration export terminal demo">
  <source src="../_static/demos/cli.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/cli.mp4>` · {download}`Text transcript <../_static/demos/cli.txt>` · {download}`Runnable script <../../examples/demos/cli.sh>` · {download}`VHS tape <tapes/cli.tape>`

(demo-workspace)=
## Workspace lifecycle

Discover environments and named targets, solve the complete matrix, validate the lock, reject a changed manifest and update one target. See {doc}`../tutorials/workspaces`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/workspace.png" aria-label="Workspace lifecycle terminal demo">
  <source src="../_static/demos/workspace.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/workspace.mp4>` · {download}`Text transcript <../_static/demos/workspace.txt>` · {download}`Runnable script <../../examples/demos/workspace.sh>` · {download}`VHS tape <tapes/workspace.tape>`

(demo-locks)=
## Saved locks and SBOMs

Extract exact workspace records, render explicit URLs, convert a generic lock and generate an SBOM with declared roots. See {doc}`../how-to/extract-workspace-lock`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/locks.png" aria-label="Saved locks and SBOMs terminal demo">
  <source src="../_static/demos/locks.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/locks.mp4>` · {download}`Text transcript <../_static/demos/locks.txt>` · {download}`Runnable script <../../examples/demos/locks.sh>` · {download}`VHS tape <tapes/locks.tape>`

(demo-http)=
## HTTP solving and retained results

Call a local service, export a lock and compare the exact bytes retrieved from its retained URL. See {doc}`../tutorials/http-api`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/http.png" aria-label="HTTP solving and retained results terminal demo">
  <source src="../_static/demos/http.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/http.mp4>` · {download}`Text transcript <../_static/demos/http.txt>` · {download}`Runnable script <../../examples/demos/http.sh>` · {download}`VHS tape <tapes/http.tape>`

(demo-cache)=
## Persistent cache

Restart the service and retrieve the same retained bytes from a file-backed cache without another solve. See {doc}`../how-to/configure-result-cache`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/cache.png" aria-label="Persistent cache terminal demo">
  <source src="../_static/demos/cache.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/cache.mp4>` · {download}`Text transcript <../_static/demos/cache.txt>` · {download}`Runnable script <../../examples/demos/cache.sh>` · {download}`VHS tape <tapes/cache.tape>`

(demo-trust)=
## Artifact verification

Verify a public signed fixture offline, then reject modified bytes and the wrong expected signer. The fixture uses staging trust and does not establish provenance claims. See {doc}`../how-to/sign-and-verify`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/trust.png" aria-label="Artifact verification terminal demo">
  <source src="../_static/demos/trust.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/trust.mp4>` · {download}`Text transcript <../_static/demos/trust.txt>` · {download}`Runnable script <../../examples/demos/trust.sh>` · {download}`VHS tape <tapes/trust.tape>`

(demo-action)=
## GitHub Action client

Execute the composite Action scripts locally against a real service and inspect a two-platform workspace lock. Hosted runner execution is checked separately by the Action smoke workflow. See {doc}`../how-to/use-github-action`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/action.png" aria-label="GitHub Action client terminal demo">
  <source src="../_static/demos/action.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/action.mp4>` · {download}`Text transcript <../_static/demos/action.txt>` · {download}`Runnable script <../../examples/demos/action.sh>` · {download}`VHS tape <tapes/action.tape>`

(demo-docker)=
## Docker service

Build the image from this checkout, launch it on a loopback-only port, check readiness and solve an environment. The image build completes before terminal recording starts. See {doc}`../how-to/run-with-docker`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/docker.png" aria-label="Docker service terminal demo">
  <source src="../_static/demos/docker.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/docker.mp4>` · {download}`Text transcript <../_static/demos/docker.txt>` · {download}`Runnable script <../../examples/demos/docker.sh>` · {download}`VHS tape <tapes/docker.tape>`

(demo-operations)=
## Diagnostics and timing

Inspect readiness, versions and capabilities, read a rejected request, and measure resolve and retained retrieval independently. The observed timings are not a service benchmark. See {doc}`../how-to/monitor-service`.

```{raw} html
<video class="presto-demo" controls preload="none" width="1100" poster="../_static/demos/operations.png" aria-label="Diagnostics and timing terminal demo">
  <source src="../_static/demos/operations.mp4" type="video/mp4">
  Your browser does not support embedded video. Use the recording download below.
</video>
```

{download}`Recording <../_static/demos/operations.mp4>` · {download}`Text transcript <../_static/demos/operations.txt>` · {download}`Runnable script <../../examples/demos/operations.sh>` · {download}`VHS tape <tapes/operations.tape>`

The scripts share {download}`common.sh <../../examples/demos/common.sh>`. Run them from a checkout so their fixtures and assertion helpers are available. Live signing requires an operator-provided identity and trust configuration. Follow {doc}`../how-to/sign-and-verify` for that workflow.
