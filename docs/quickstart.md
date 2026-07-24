# Quick start

This tutorial installs conda-presto in a dedicated tooling environment, runs
one solve, and starts the optional HTTP server. The solve does not create or
modify a target environment.

## Install

Choose the package source that matches the documentation you want to follow.

`````{tab-set}

````{tab-item} Latest release
```bash
conda create --name conda-presto \
  --override-channels \
  --channel conda-forge \
  python=3.13 \
  'conda>=26.5,<27' \
  'conda-rattler-solver>=0.1.1,<0.2' \
  'conda-lockfiles>=0.2.1' \
  'jinja2>=3.1.2' \
  'litestar>=2.18' \
  'pyjwt>=2.0' \
  'uvicorn>=0.34' \
  'brotli-python>=1.1' \
  pip
conda activate conda-presto
python -m pip install conda-presto
```
````

````{tab-item} Current main
```bash
conda create --name conda-presto-main \
  --override-channels \
  --channel conda-forge \
  python=3.13 \
  'conda>=26.5,<27' \
  'conda-rattler-solver>=0.1.1,<0.2' \
  'conda-lockfiles>=0.2.1' \
  'jinja2>=3.1.2' \
  'litestar>=2.18' \
  'pyjwt>=2.0' \
  'uvicorn>=0.34' \
  'brotli-python>=1.1' \
  pip
conda activate conda-presto-main
python -m pip install 'git+https://github.com/jezdez/conda-presto.git@main'
```
````

````{tab-item} Source checkout
```bash
git clone https://github.com/jezdez/conda-presto.git
cd conda-presto
pixi install
pixi shell
```
````

`````

conda-presto is installed from PyPI or Git into a conda environment that
supplies conda and its solver plugins. It is not currently published as a conda
package. The source-checkout path enters the default Pixi development
environment. Run the remaining commands inside the activated conda environment
or Pixi shell.

:::{note}
The documentation site is built from `main`. If the `[Unreleased]` changelog
section is not empty, use the current-main or source-checkout tab for those
changes.
:::

conda-presto requires conda 26.5 or newer and Python 3.13 or newer. Verify that
conda discovered the subcommand:

```bash
conda presto --help
```

## Resolve an environment

Resolve Python and NumPy for Linux without downloading packages or creating a
prefix:

```bash
conda presto -c conda-forge -p linux-64 python=3.13 numpy > result.json
```

Inspect the selected package names and versions:

```bash
jq -r '.[0].packages[] | "\(.name) \(.version) \(.build)"' result.json
```

The outer array contains one result per requested platform. A successful entry
has package records and an `error` value of `null`.

## Resolve an environment file

Given an `environment.yml`, request two platforms:

```bash
conda presto -f environment.yml -p linux-64 -p osx-arm64 > result.json
```

The two solves can run in parallel. To write a lockfile instead of native JSON,
select a conda exporter:

```bash
conda presto -f environment.yml \
  -p linux-64 \
  -p osx-arm64 \
  --format pixi-lock-v6 > pixi.lock
```

## Start the HTTP server

Start a local server in one terminal:

```bash
conda presto --serve
```

Check readiness from another terminal:

```bash
curl -sS http://127.0.0.1:8000/health
```

The response is `{"status":"ok"}` when the server can accept solve requests.
The 0.8.0 release and current main serve the browser workbench at
`http://127.0.0.1:8000/`. Fetch
`http://127.0.0.1:8000/openapi.json` for the generated OpenAPI document.

## Choose the next guide

- {doc}`tutorials/cli-resolve` builds a multi-platform lockfile from start to finish.
- {doc}`tutorials/http-api` introduces the HTTP workflow and result permalinks.
- {doc}`tutorials/workbench` introduces the browser workflow.
- {doc}`tutorials/review-and-repair` teaches preflight, repair, diff, and explain.
- {doc}`tutorials/local-service` introduces the broker service and Presto solver.
- {doc}`how-to/index` contains task-focused CLI, server, Docker, cache, and CI guides.
- {doc}`reference/index` documents the exact interfaces and configuration.
