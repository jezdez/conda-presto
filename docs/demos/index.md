# Demos

Recordings appear beside the workflows they demonstrate. Each has a static preview for reduced motion and a downloadable transcript from a complete checked example run. Package versions, identifiers and timings depend on the recording environment.

## User workflows

| Workflow | What it shows |
|---|---|
| {ref}`demo-cli` | Resolve requirements and render dependency declarations. |
| {ref}`demo-workspace` | Discover named targets, solve a workspace, validate its lock and update one target. |
| {ref}`demo-locks` | Extract saved records, convert a generic lock and generate an SBOM with declared roots. |

## Services

| Workflow | What it shows |
|---|---|
| {ref}`demo-http` | Resolve through HTTP and retrieve exact retained bytes. |
| {ref}`demo-cache` | Retrieve retained results after restarting a service with file storage. |
| {ref}`demo-docker` | Build and run the server image, then resolve a Linux environment. |
| {ref}`demo-operations` | Inspect readiness and capabilities, diagnose rejected input and observe local timings. |

## Integrations

| Workflow | What it shows |
|---|---|
| {ref}`demo-trust` | Verify a public signed fixture offline and reject changed bytes or the wrong signer. |
| {ref}`demo-action` | Run the GitHub Action client locally against a real service. |

The workspace features require the source checkout described in {doc}`../tutorials/workspaces`. Solving requires public channel access. The trust recording needs no signing credentials. The Action recording demonstrates local client execution, while the hosted smoke workflow checks the GitHub runner separately.

## Run or record an example

From the repository root:

```bash
pixi run --locked -e examples examples-check
pixi run --locked -e examples bash demos/check.sh docker
pixi run --locked -e demos demos workspace
```

The first command checks all runnable examples except Docker. The second checks Docker separately. The third records one demo. Pass several names or omit them to record all nine, including Docker.

The locked recording environment supplies VHS, ttyd, FFmpeg and `bat`. VHS locates or downloads a Chromium browser. Docker recording also requires a running daemon. Scripts use temporary files and local services, then clean them up on exit. The locally built `conda-presto:docs-demo` image remains available for reuse.

Shared settings, tapes, fixtures, recordings and transcripts live together in the repository's `demos/` directory. See its {download}`recording guide <../../demos/README.md>` for organization and prerequisites. Review regenerated media before committing it. CI checks the runnable examples and builds documentation from committed media.
