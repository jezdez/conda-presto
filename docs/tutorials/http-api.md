# HTTP API

This tutorial covers the conda-presto HTTP API: resolving specs,
uploading environment files, converting between lockfile formats, and
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

Upload an environment file directly. The Content-Type header tells
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
