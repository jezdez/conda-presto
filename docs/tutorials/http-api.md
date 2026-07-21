# Resolve through the HTTP API

This tutorial starts a local server, submits a solve request, follows a cached
result location, and writes a lockfile from an uploaded environment file.

## Start the server

Install conda-presto with {doc}`../quickstart`. In one terminal, start the
server:

```bash
conda presto --serve
```

In another terminal, set the base URL and wait for readiness:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
curl -sS "$CONDA_PRESTO_URL/health"
```

Continue after the response reports `{"status":"ok"}`.

## Submit a JSON request

Resolve Python and NumPy for Linux:

```bash
curl -sS "$CONDA_PRESTO_URL/resolve" \
  --json '{
    "specs": ["python=3.13", "numpy"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' > result.json
```

Inspect the platform and package count:

```bash
jq '.[0] | {platform, package_count: (.packages | length), error}' result.json
```

The HTTP native JSON shape matches the CLI native JSON shape.

## Inspect result caching

Repeat the request while saving response headers:

```bash
curl -sS -D headers.txt "$CONDA_PRESTO_URL/resolve" \
  --json '{
    "specs": ["python=3.13", "numpy"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }' > result.json

rg -i '^(location|cache-control):' headers.txt
```

When the response can be retained, `Location` contains a relative `/r/<hash>`
path. Fetch that immutable snapshot while it remains in the configured cache:

```bash
location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')
curl -sS "$CONDA_PRESTO_URL$location" | jq '.[0].platform'
```

A later `POST /resolve` checks current repodata before reusing a result. The
permalink itself returns the stored snapshot without revalidating repodata.

## Upload an environment file

Create an `environment.yml`:

```yaml
channels:
  - conda-forge
dependencies:
  - python=3.13
  - numpy
```

Upload it and ask the exporter for a `pixi.lock`:

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?platform=linux-64&format=pixi-lock-v6" \
  -o pixi.lock
```

The media type selects raw file handling. The `filename` query parameter can
select a more specific parser when the media type is ambiguous.

## Explore the generated API documentation

Open `$CONDA_PRESTO_URL/` in a browser for the interactive Scalar interface, or
download the generated OpenAPI schema:

```bash
curl -sS "$CONDA_PRESTO_URL/openapi.json" > openapi.json
```

:::{note}
The generated schema does not describe the manually dispatched request bodies
for `POST /resolve`, `POST /preflight`, or `POST /transcode`. Use the
{doc}`../reference/http-api` page for those body contracts.
:::

## What you learned

- JSON and raw file bodies use the same resolve operation.
- Native HTTP results have the same per-platform shape as native CLI results.
- Retained results receive a content-addressed location.
- Exporter output can be written directly to a lockfile.

For independent HTTP tasks, see {doc}`../how-to/call-http-api`. Exact endpoint
schemas and status codes are in {doc}`../reference/http-api`.
