# Use conda-presto in GitHub Actions

The `jezdez/conda-presto` composite Action can run the checked-out
conda-presto CLI on a runner or call an existing HTTP deployment.

Pin the Action to a released tag. The examples use the latest released version
at the time this page was written.

## Resolve a checked-in environment file

Check out the repository before passing a file to local mode:

```yaml
name: Resolve environment

on:
  pull_request:
    paths:
      - environment.yml

jobs:
  solve:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: jezdez/conda-presto@v0.8.0
        with:
          file: environment.yml
          platforms: linux-64,osx-arm64
```

Local mode is the default. The Action uses a commit-pinned `setup-pixi` action
to install Pixi 0.70.1 and runs conda-presto from the pinned Action checkout.
It does not require a running server.

## Write and upload a lockfile

Set `format` and `output`, then use the written file in later steps:

```yaml
jobs:
  lock:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: jezdez/conda-presto@v0.8.0
        id: solve
        with:
          file: environment.yml
          platforms: linux-64,osx-arm64
          format: pixi-lock-v6
          output: pixi.lock

      - uses: actions/upload-artifact@v4
        if: steps.solve.outputs.solved == 'true'
        with:
          name: pixi-lock
          path: pixi.lock
```

The `solved` output is `false` when native JSON contains a platform error. A
request that exits unsuccessfully fails the Action step.

## Resolve inline specs

Pass comma-separated specs, channels, and platforms:

```yaml
- uses: jezdez/conda-presto@v0.8.0
  with:
    specs: python=3.13,numpy,pandas
    channels: conda-forge
    platforms: linux-64,osx-arm64
```

The Action has no `command` input. Resolving is its only operation.

## Call a hosted deployment

Remote mode sends a JSON request to the deployment's `/resolve` endpoint:

```yaml
- uses: actions/checkout@v4

- uses: jezdez/conda-presto@v0.8.0
  with:
    mode: remote
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
    file: environment.yml
    platforms: linux-64,osx-arm64
    format: conda-lock-v1
    output: conda-lock.yml
```

Store the base URL without a trailing slash in the repository or organization
variable `CONDA_PRESTO_URL`. Use a secret instead if the URL itself contains
sensitive information.

Remote mode requires `jq` and `curl` on the runner. GitHub-hosted Ubuntu
runners provide both tools. It sends the complete environment file and channel
configuration to the endpoint. Use only a trusted HTTPS deployment whose
operators are permitted to read that data.

The Action rejects plain HTTP except for `localhost`, `127.0.0.1`, and `::1`.
It disables curl's default `curlrc` configuration, accepts only HTTP 2xx, and
validates the native JSON result shape when no exporter format is selected.
It does not print response bodies automatically. Use `output` to write the body
to a workspace file, or read the bounded `result` output in a later step.

## Read outputs in another step

Give the solve step an ID, then use its `solved` and `result` outputs:

```yaml
- uses: jezdez/conda-presto@v0.8.0
  id: solve
  with:
    specs: python=3.13,numpy
    platforms: linux-64

- name: Report result
  if: always()
  env:
    SOLVED: ${{ steps.solve.outputs.solved }}
  run: echo "solved=${SOLVED}"
```

Large response bodies are not copied into `result`. Set `output` when another
step needs a large solve or lockfile result.

At least one of `file` or `specs` must normally provide solve input. Do not add
spaces around comma-separated values because the Action passes each value
through without trimming it.

Use {doc}`../reference/github-action` for the complete input, output, and
failure contract. Use {doc}`../reference/output-formats` to choose a format and
{doc}`call-http-api` to inspect the remote request contract.
