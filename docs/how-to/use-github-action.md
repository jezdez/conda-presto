# Use conda-presto in GitHub Actions

Configure the repository variable `CONDA_PRESTO_URL` with your service's HTTPS base URL. The Action sends environment files and channel settings to that service.

The example targets current main. Pin a reviewed commit for a production workflow.

```yaml
name: Resolve environment

on:
  pull_request:
    paths:
      - environment.yml

jobs:
  lock:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: jezdez/conda-presto@main
        id: solve
        with:
          endpoint: ${{ vars.CONDA_PRESTO_URL }}
          file: environment.yml
          platforms: linux-64,osx-arm64
          format: rattler-lock-v6
          output: pixi.lock

      - uses: actions/upload-artifact@v4
        if: steps.solve.outputs.solved == 'true'
        with:
          name: pixi-lock
          path: pixi.lock
```

For inline input, replace `file` with `specs: python=3.13,numpy`. Values in `specs`, `channels`, `environments` and `platforms` are comma-separated without surrounding spaces. Omitted channels use the input file or service defaults.

For the workspace manifest in {doc}`parse-workspace`, select named environments and request a combined lock:

```yaml
- uses: jezdez/conda-presto@main
  id: workspace
  with:
    endpoint: ${{ vars.CONDA_PRESTO_URL }}
    file: conda.toml
    environments: default,test
    platforms: linux-64,osx-arm64
    format: conda-workspaces-lock-v1
    output: conda.lock

- uses: actions/upload-artifact@v4
  if: steps.workspace.outputs.solved == 'true'
  with:
    name: conda-lock
    path: conda.lock
```

A complete {download}`workspace workflow <../../demos/action/workflow.yml>` and its {download}`manifest <../../demos/action/conda.toml>` are available in the repository. The hosted Action smoke test uses that same manifest.

The lock contains all four selected solutions. Omit `environments` and `platforms` to solve every declared environment and target. Workspace requests use manifest channels and do not accept additional `specs` or `channels`.

GitHub-hosted Ubuntu runners provide the required `curl` and `jq` tools. Plain HTTP is accepted only for loopback servers. The Action validates HTTP success and native platform errors, and leaves response bodies out of logs. Use the `output` file for large results and the bounded `result` output for small responses.

See {doc}`../reference/github-action` for the input and failure behavior, and {doc}`../reference/output-formats` to select a format.
