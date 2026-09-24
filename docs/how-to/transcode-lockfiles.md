# Export lockfiles without solving

Watch the {ref}`demo-locks` demo and follow the commands below.

For named environment extraction from workspace `conda.lock` files, use {doc}`extract-workspace-lock`. That workflow uses explicit selection and preserves source lock metadata.

Use the export operation when an existing lockfile contains every requested platform and the output is a supported lockfile format.

The examples use a `pixi.lock` previously written through conda-presto by
conda-lockfiles' `pixi-lock-v6` exporter. Other lockfile versions require an
installed conda environment-specifier plugin that supports them.

## Export from the CLI

Pass one input lockfile, its covered platforms, and a lockfile exporter:

```bash
conda presto \
  --export \
  --file pixi.lock \
  --platform linux-64 \
  --platform osx-arm64 \
  --format conda-lock-v1 \
  > conda-lock.yml
```

Export mode uses embedded package metadata and requires all of these conditions:

- exactly one input file is provided
- that input is recognized as a lockfile
- every requested platform is present
- the output format is recognized as a lockfile
- no inline specs are added
- channels are not overridden

Unsupported input, missing platforms and extra specs or channels produce an error. Export mode does not fall back to a solve or download package archives. The ordinary CLI solve path retains its older lockfile fast path, which can materialize package records through the installed parser.

## Export through the HTTP API

`POST /export` uses the same no-download conversion. Conda-presto's compatibility adapter builds and serializes temporary package records inside the isolated parser process using only metadata already present in the upload:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000

curl --fail-with-body --silent --show-error \
  --data-binary @pixi.lock \
  --header 'Content-Type: application/yaml' \
  "$CONDA_PRESTO_URL/export?filename=pixi.lock&platform=linux-64&platform=osx-arm64&format=conda-lock-v1" \
  --output conda-lock.yml
```

Use `filename` when the media type does not identify the lockfile parser.
The no-fetch path currently targets the `conda-lock-v1` and
`rattler-lock-v6` exporters supplied by conda-lockfiles, including their
aliases.

`POST /transcode` remains available with the same lock-to-lock behavior and restrictions. Use `/export` for the general no-solve export operation, including declarations and saved workspace records.

:::{note}
The temporary compatibility path rejects source data it cannot carry through
conda's environment model without changing package selection or solver
constraints. This can apply even when the source and target formats match. See
{ref}`http-transcode` for the exact rejection cases.
:::

## Diagnose a rejected conversion

`--fail-with-body` prints the HTTP error response. The `reasons` list
identifies missing platforms, a non-lockfile input or output, or request fields
that would require a solve.

Available lockfile exporters are listed in
{doc}`../reference/output-formats`. For a normal environment-to-lockfile solve,
use {doc}`resolve-from-cli` or {doc}`/tutorials/http-api`.
