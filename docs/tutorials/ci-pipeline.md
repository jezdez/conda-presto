# CI pipeline

This tutorial shows how to use the `jezdez/conda-presto` GitHub
Action to resolve environments in CI. The action supports two modes:
local (installs on the runner) and remote (calls a hosted endpoint).

## Local mode

Local mode is the default. It installs conda-presto on the runner
via pixi and runs the CLI directly. No infrastructure required.

### Basic solve

```yaml
- uses: jezdez/conda-presto@v0.4.0
  with:
    command: solve
    file: environment.yml
    platforms: linux-64,osx-arm64
```

### Writing a lockfile

Add `format` and `output` to write the result to a file:

```yaml
- uses: jezdez/conda-presto@v0.4.0
  with:
    command: solve
    file: environment.yml
    platforms: linux-64
    format: pixi-lock-v6
    output: pixi.lock
```

The file is written to the workspace, so subsequent steps can use it
as an artifact or pass it to `conda env create`.

### Inline specs

Instead of an environment file, pass specs directly:

```yaml
- uses: jezdez/conda-presto@v0.4.0
  with:
    command: solve
    specs: python=3.12,numpy,pandas
    channels: conda-forge
    platforms: linux-64,osx-arm64
```

## Remote mode

Remote mode calls a hosted conda-presto deployment. This is faster
for teams that already run a server (shared repodata cache, no
install step on the runner).

```yaml
- uses: jezdez/conda-presto@v0.4.0
  with:
    mode: remote
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
    command: solve
    file: environment.yml
    platforms: linux-64
```

Store the endpoint URL as a repository variable (`CONDA_PRESTO_URL`)
rather than hardcoding it.

### Remote with lockfile output

```yaml
- uses: jezdez/conda-presto@v0.4.0
  with:
    mode: remote
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
    command: solve
    file: environment.yml
    platforms: linux-64,osx-arm64,win-64
    format: conda-lock-v1
    output: conda-lock.yml
```

## Full workflow example

A complete workflow that resolves an environment and uploads the
lockfile as an artifact:

```yaml
name: Resolve environment
on:
  push:
    paths: [environment.yml]

jobs:
  solve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: jezdez/conda-presto@v0.4.0
        id: solve
        with:
          command: solve
          file: environment.yml
          platforms: linux-64,osx-arm64
          format: pixi-lock-v6
          output: pixi.lock

      - uses: actions/upload-artifact@v4
        if: steps.solve.outputs.solved == 'true'
        with:
          name: lockfile
          path: pixi.lock
```

## Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `mode` | no | `local` | `local` (install + CLI) or `remote` (HTTP API) |
| `command` | yes | `solve` | Action to perform |
| `file` | no | | Path to an environment file |
| `specs` | no | | Comma-separated package specs |
| `channels` | no | `conda-forge` | Comma-separated channels |
| `platforms` | no | | Comma-separated target platforms |
| `format` | no | | Output format name |
| `output` | no | | Path to write the response body to |
| `endpoint` | remote only | | conda-presto base URL (required when `mode: remote`) |

## Outputs

| Output | Description |
|---|---|
| `result` | The response body (JSON by default, or the formatted output when `format` is set) |
| `solved` | `true` if all platforms resolved successfully, `false` otherwise |
