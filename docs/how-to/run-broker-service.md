# Run a broker-managed local service

conda-presto registers `conda-presto.server` as a manual conda-broker service.
Use it for repeated local HTTP requests or as the service behind the internal
Presto solver backend.

The service binds to a broker-assigned loopback port. It does not start
automatically and does not change one-shot `conda presto` commands.

## Install and verify the provider

Use the latest-release installation in {doc}`../quickstart`, then verify the
provider from that activated conda environment:

```bash
conda broker list
```

In a source checkout, use the production Pixi environment:

```bash
pixi install --environment prod
pixi shell --environment prod
conda broker list
```

Run the remaining commands in that Pixi shell.

The list should contain `conda-presto.server`. If it does not, continue with
{doc}`troubleshoot`.

## Start and wait for readiness

Start the service explicitly, then allow enough time for an initial repodata
download:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

`wait` succeeds only after `/health` reports that the persistent solver worker
has loaded its configured indexes.

## Obtain the endpoint

Display the endpoint for interactive use:

```bash
conda broker endpoint conda-presto.server
```

For a shell script, request JSON and extract the resolved URL:

```bash
export CONDA_PRESTO_URL="$(
  conda broker endpoint conda-presto.server --json \
    | jq --raw-output '.endpoint.url'
)"

curl --fail --silent --show-error "$CONDA_PRESTO_URL/health"
```

Use the endpoint with the public API:

```bash
curl --fail --silent --show-error \
  "$CONDA_PRESTO_URL/resolve" \
  --json '{"specs":["python=3.13"],"platforms":["linux-64"]}' \
  | jq
```

## Inspect status, events, and logs

Read the current service state:

```bash
conda broker status conda-presto.server
conda broker status conda-presto.server --json
```

Follow broker lifecycle events and service output in separate terminals:

```bash
conda broker events --follow
```

```bash
conda broker logs conda-presto.server --follow
```

See {doc}`monitor-service` for readiness and refresh-log interpretation.

## Apply environment changes

conda-broker captures the parent environment when its daemon starts.
Restarting only `conda-presto.server` does not import variables exported later
in the shell. Stop the broker before changing service settings:

```bash
conda broker stop
export CONDA_PRESTO_CHANNELS=conda-forge,bioconda
export CONDA_PRESTO_PLATFORMS=linux-64,osx-arm64
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

Use {doc}`configure-result-cache` and {doc}`refresh-solver-cache` for cache
settings that must be present at service startup.

## Stop or restart the service

Stop only conda-presto while leaving the broker available:

```bash
conda broker stop conda-presto.server
```

Restart it with the environment already held by the broker daemon:

```bash
conda broker restart conda-presto.server
conda broker wait conda-presto.server --timeout 180
```

## Trust boundary

Loopback prevents remote network access, but it does not authenticate users on
the same host. Run the service only on a trusted single-user system or behind
equivalent process isolation. Keep private-channel credentials and persistent
cache data inside that boundary. The solver route rejects browser-originated,
non-JSON, wrong-authority, and non-loopback requests, but another local process
that can discover the endpoint remains inside the same trust boundary.

Read {doc}`../explanation/security` before using private channels. Use
{doc}`use-presto-solver` to select the local solver backend. See
{doc}`../reference/broker-service` for the provider and health-check contract.
