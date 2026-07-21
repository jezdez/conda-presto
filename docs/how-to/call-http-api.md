# Call the HTTP API

Use the public HTTP API for applications and scripts that share a running
conda-presto service.

Set the server URL once for the examples:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
curl --fail --silent --show-error "$CONDA_PRESTO_URL/health"
```

## Resolve inline specs with GET

Let curl encode repeated query parameters:

```bash
curl --fail --silent --show-error --get \
  "$CONDA_PRESTO_URL/resolve" \
  --data-urlencode 'spec=python=3.13' \
  --data-urlencode 'spec=numpy' \
  --data-urlencode 'channel=conda-forge' \
  --data-urlencode 'platform=linux-64' \
  | jq
```

## Resolve a JSON request with POST

Use a JSON body when the request is assembled programmatically:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/resolve" \
  --json '{
    "specs": ["python=3.13", "numpy"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64", "osx-arm64"]
  }' \
  | jq '.[].platform'
```

Body fields take precedence over matching query parameters. The output format
is always selected with the `format` query parameter.

## Upload an environment file

Send the original bytes and a matching media type:

```bash
curl --fail --silent --show-error \
  --data-binary @environment.yml \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?platform=linux-64"
```

Use `filename` when a generic YAML media type could describe several installed
formats. This example selects the pixi-lock parser before transcoding:

```bash
curl --fail --silent --show-error \
  --data-binary @pixi.lock \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/transcode?filename=pixi.lock&platform=linux-64&format=conda-lock-v1" \
  --output conda-lock.yml
```

Accepted raw upload types and the full request schema are listed in
{doc}`../reference/http-api`.

## Write a lockfile response

Select an exporter and write the response directly to a file:

```bash
curl --fail --silent --show-error \
  --data-binary @environment.yml \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/resolve?platform=linux-64&platform=osx-arm64&format=pixi-lock-v6" \
  --output pixi.lock
```

Exporter responses fail as a whole when one platform cannot be solved. Native
JSON instead keeps per-platform errors in the response array.

## Capture a content-addressed result

Inspect the response headers to obtain the relative cache location:

```bash
curl --fail --silent --show-error \
  --dump-header response-headers.txt \
  --output solve-result.json \
  --get "$CONDA_PRESTO_URL/resolve" \
  --data-urlencode 'spec=zlib' \
  --data-urlencode 'channel=conda-forge' \
  --data-urlencode 'platform=linux-64'

grep -i '^location:' response-headers.txt
```

See {doc}`configure-result-cache` for persistent storage and retrieval through
`/r/<hash>`.

## Inspect the generated API documentation

Open the server root in a browser for the Scalar interface or fetch the
generated OpenAPI document:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/openapi.json" \
  --output openapi.json
```

:::{note}
The generated schema does not describe the manually dispatched request bodies
for `POST /resolve`, `POST /preflight`, or `POST /transcode`. Use
{doc}`../reference/http-api` for those body contracts.
:::

Use {doc}`inspect-environments` for preflight, repair, diff, and explain tasks.
Use {doc}`transcode-lockfiles` when conversion does not require a solve.
