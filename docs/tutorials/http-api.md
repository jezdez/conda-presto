# HTTP API

This tutorial covers the conda-presto HTTP API: resolving specs,
uploading input files, converting between lockfile formats, and
inspecting the server.

```{note}
All examples use `$CONDA_PRESTO_URL` as a placeholder for your
server's base URL. Set it before running the commands:

    export CONDA_PRESTO_URL=https://your-presto-instance.example.com

Replace the value with whatever URL your deployment uses.
```

## Quick resolve

The simplest API call resolves inline specs via query parameters:

```bash
curl -sS "$CONDA_PRESTO_URL/resolve?spec=numpy&platform=linux-64&platform=osx-arm64" | jq '.[].platform'
```

This returns a JSON array with one entry per platform, the same shape
the CLI emits.

## Resolving from files

Upload an input file directly. The Content-Type header tells
the server which parser to use.

`````{tab-set}

````{tab-item} environment.yml
```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?platform=linux-64&platform=osx-arm64"
```
````

````{tab-item} pixi.toml
```bash
curl -sS --data-binary @pixi.toml \
  -H 'Content-Type: application/toml' \
  "$CONDA_PRESTO_URL/resolve?format=pixi-lock-v6"
```
````

````{tab-item} pyproject.toml
For `pyproject.toml` with embedded pixi or conda metadata, add
`filename=pyproject.toml` so the server picks the right parser:

```bash
curl -sS --data-binary @pyproject.toml \
  -H 'Content-Type: application/toml' \
  "$CONDA_PRESTO_URL/resolve?filename=pyproject.toml&format=conda-lock-v1&platform=linux-64&platform=osx-arm64" \
  -o conda-lock.yml
```
````

````{tab-item} requirements.txt
```bash
curl -sS --data-binary @requirements.txt \
  -H 'Content-Type: text/plain' \
  "$CONDA_PRESTO_URL/resolve?filename=requirements.txt&platform=linux-64&format=pixi-lock-v6" \
  -o pixi.lock
```
````

`````

## JSON POST

For programmatic use, POST a JSON body with specs, channels, and
platforms:

```bash
curl -sS --json '{
  "specs": ["python=3.12", "polars", "pyarrow", "duckdb"],
  "channels": ["conda-forge"],
  "platforms": ["linux-64", "osx-arm64", "win-64"]
}' "$CONDA_PRESTO_URL/resolve" | jq '.[].platform'
```

`curl --json` sets `Content-Type: application/json` automatically.
Body fields override query parameters when both are present.

## Reviewing a proposed environment

Run local checks before asking the solver for metadata. Preflight accepts the
same request body as `/resolve`, but it never contacts channels or attempts a
solve:

```bash
curl -sS "$CONDA_PRESTO_URL/preflight" \
  --json '{"specs":["python=3.13","numpy"],"channels":["conda-forge"]}' | jq
```

When a solve is infeasible, ask `/repair` for single-spec changes rather than
changing the input automatically. The endpoint tests every returned suggestion
on every requested platform:

```bash
curl -sS "$CONDA_PRESTO_URL/repair?max_suggestions=3" \
  --json '{"specs":["scipy==1.5"],"channels":["conda-forge"],"platforms":["linux-64"]}' | jq
```

The endpoint initially relaxes exact pins and one side of simple bounded
version ranges. A `partial` response includes the suggestions found before a
server limit stopped the search.

Compare two revisions with `/diff`. Its top-level `platforms` list applies to
both inputs, and lockfile inputs are read directly when they already contain
that platform:

```bash
curl -sS "$CONDA_PRESTO_URL/diff" \
  --json '{"from":{"specs":["python=3.12"]},"to":{"specs":["python=3.13"]},"platforms":["linux-64"]}' | jq '.diff["linux-64"]'
```

To see why a package is present, ask for one platform and follow the returned
chains from requested specs to the package:

```bash
curl -sS "$CONDA_PRESTO_URL/explain" \
  --json '{"package":"zlib","specs":["python"],"platforms":["linux-64"]}' | jq '.chains'
```

## Output formats

Add `?format=` to route the response through conda's exporter
plugins. This works with both GET and POST requests, and with
file uploads.

### Writing a pixi.lock

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?format=pixi-lock-v6&platform=linux-64&platform=osx-arm64" \
  -o pixi.lock
```

### Writing a conda-lock.yml

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?format=conda-lock-v1&platform=linux-64&platform=osx-arm64&platform=win-64" \
  -o conda-lock.yml
```

### Writing an explicit lockfile

```bash
curl -sS \
  "$CONDA_PRESTO_URL/resolve?spec=python%3D3.12&spec=pytorch&spec=torchvision&platform=linux-64&format=explicit" \
  -o pytorch.explicit.txt
```

### Converting an existing lockfile

Use `/transcode` when the input and output are both lockfiles. The
server reuses the package records already present in the uploaded
lockfile and never invokes the solver:

```bash
curl -sS --data-binary @pixi.lock \
  -H 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/transcode?filename=pixi.lock&platform=linux-64&format=conda-lock-v1" \
  -o conda-lock.yml
```

The request fails with HTTP 400 if `pixi.lock` does not contain the
requested platform, if the output format is not a lockfile, or if extra
specs or channel overrides would require a solve.

```{tip}
The full lockfile pipeline works in a single shell session: resolve
remotely, then create the environment locally.

    curl -sS --data-binary @environment.yml \
      -H 'Content-Type: application/yaml' \
      "$CONDA_PRESTO_URL/resolve?format=pixi-lock-v6&platform=linux-64" \
      -o pixi.lock
    conda env create -n demo -f pixi.lock
```

## Overriding channels and platforms

Query parameters can override or extend whatever is declared in the
uploaded file:

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?channel=conda-forge&channel=bioconda&platform=linux-64&format=environment-yaml" \
  -o environment.resolved.yml
```

## Server introspection

### OpenAPI schema

The full OpenAPI 3.1 schema is available at `/openapi.json`. The
interactive Scalar UI is at `/`.

```bash
curl -sS "$CONDA_PRESTO_URL/openapi.json" | jq '{version: .info.version, paths: (.paths | keys)}'
```

### Formats

List all registered output format names:

```bash
curl -sS "$CONDA_PRESTO_URL/formats"
```

### Platforms

List all known conda platform subdirs:

```bash
curl -sS "$CONDA_PRESTO_URL/platforms"
```

### Version

Returns version info for conda-presto and its key dependencies:

```bash
curl -sS "$CONDA_PRESTO_URL/version"
```

### Health

Simple liveness check:

```bash
curl -sS "$CONDA_PRESTO_URL/health"
```
