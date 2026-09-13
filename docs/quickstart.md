# Quick start

Install the package into a conda environment with its solver and lockfile providers:

```bash
conda create --name conda-presto --channel conda-forge --override-channels \
  python=3.13 'conda>=26.5,<27' 'conda-rattler-solver>=0.1.1,<0.2' \
  'conda-lockfiles>=0.2.1' pip
conda activate conda-presto
python -m pip install 'conda-presto[server]'
```

For current source, use `python -m pip install '.[server]'` from its checkout instead. From a Pixi checkout, `pixi run serve` starts the development server.

## Start the server

```bash
CONDA_PRESTO_PLATFORMS=linux-64 conda presto --serve
```

In another terminal, wait for readiness and send a request:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail --silent --show-error \
  --dump-header headers.txt --output result.json \
  --json '{"specs":["zlib"],"channels":["conda-forge"],"platforms":["linux-64"]}' \
  http://127.0.0.1:8000/resolve
```

`result.json` contains selected packages and any per-platform error. The service does not install them. Add `?format=rattler-lock-v6` to request a lockfile instead.

## Retrieve a retained result

When retention succeeds, the response has a `Location` header. Retrieve those exact bytes while the entry remains available:

```bash
location=$(awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' headers.txt)
if test -n "$location"
then
  curl --fail --silent --show-error "http://127.0.0.1:8000$location" --output retained.json
  cmp result.json retained.json
fi
```

Read {doc}`tutorials/http-api` for exporting, SBOMs, signing and verification, {doc}`how-to/run-with-docker` for container deployment, or {doc}`reference/http-api` for exact fields. The OpenAPI document is at `/openapi.json`.
