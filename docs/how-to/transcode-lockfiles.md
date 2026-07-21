# Transcode lockfiles without solving

Use the lockfile fast path when an existing lockfile already contains every
requested platform and the output is another lockfile format.

The examples use a `pixi.lock` previously written by conda-presto's
`pixi-lock-v6` exporter. Other lockfile versions require an installed conda
environment-specifier plugin that supports them.

## Transcode from the CLI

Pass one input lockfile, its covered platforms, and a lockfile exporter:

```bash
conda presto \
  --file pixi.lock \
  --platform linux-64 \
  --platform osx-arm64 \
  --format conda-lock-v1 \
  > conda-lock.yml
```

The CLI reuses the parsed package records and does not invoke the solver only
when all of these conditions hold:

- exactly one input file is provided
- that input is recognized as a lockfile
- every requested platform is present
- the output format is recognized as a lockfile
- no inline specs are added
- channels are not overridden

If those conditions do not hold, the normal CLI path may solve the combined
request instead.

## Transcode through the HTTP API

The `/transcode` endpoint is stricter. It rejects a request that would require
a solve:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000

curl --fail --silent --show-error \
  --data-binary @pixi.lock \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/transcode?filename=pixi.lock&platform=linux-64&platform=osx-arm64&format=conda-lock-v1" \
  --output conda-lock.yml
```

Use `filename` when the media type does not identify the lockfile parser.

## Diagnose a rejected transcode

Remove `--fail` temporarily to inspect the HTTP 400 response:

```bash
curl --silent --show-error \
  --data-binary @pixi.lock \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/transcode?filename=pixi.lock&platform=win-64&format=conda-lock-v1" \
  | jq
```

The `reasons` list identifies missing platforms, a non-lockfile input or
output, or request fields that would require a solve.

Available lockfile exporters are listed in
{doc}`../reference/output-formats`. For a normal environment-to-lockfile solve,
use {doc}`resolve-from-cli` or {doc}`call-http-api`.
