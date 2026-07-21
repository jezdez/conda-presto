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

## Understand the HTTP boundary

The HTTP parser recognizes lockfile format and platform metadata but does not
materialize package records. Current environment-specifier plugins can fetch
the package URLs while building those records, which is not safe for an
untrusted upload. Use the CLI for the conversion itself.

`POST /transcode` reports this boundary as HTTP 400:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000

curl --silent --show-error \
  --data-binary @pixi.lock \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/transcode?filename=pixi.lock&platform=linux-64&platform=osx-arm64&format=conda-lock-v1" \
  | jq
```

Use `filename` when the media type does not identify the lockfile parser.

## Diagnose a rejected transcode

The normal materialization rejection is:

```json
{
  "error": "Request cannot be transcoded",
  "reasons": ["lockfile package records cannot be loaded from HTTP input"]
}
```

The `reasons` list can instead identify missing platforms, a non-lockfile input
or output, or request fields that would require a solve.

Available lockfile exporters are listed in
{doc}`../reference/output-formats`. For a normal environment-to-lockfile solve,
use {doc}`resolve-from-cli` or {doc}`call-http-api`.
