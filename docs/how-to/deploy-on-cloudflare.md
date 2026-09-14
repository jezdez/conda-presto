# Run Presto on Cloudflare

The Python adapter in `deploy/cloudflare` routes the HTTP API to a fixed pool of two native Presto containers. It publishes eligible retained outputs to R2 before returning their URLs. Local integration verifies native solves, lockfiles, SBOMs and retained reads after both containers stop. Hosted operation and geographic execution still need a deployment trial.

The entrypoint, `src/entry.py`, uses `WorkerEntrypoint` and `DurableObject`. It calls `ctx.container` directly for startup, readiness and idle shutdown. Application code is Python, while Node supplies Wrangler deployment tooling. The native CPython image continues to run Presto and conda.

## Run locally

Use Python 3.13 or newer, uv, Node.js 22.18 or newer, npm, and a running Docker-compatible engine that can execute `linux/amd64` images. From the repository root:

```bash
cd deploy/cloudflare
npm ci
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run pywrangler deploy --dry-run
uv run pywrangler dev
```

`pytest` exercises the adapter in workerd. The deployment dry run prepares the Python Worker and native image without publishing them. `pywrangler dev` builds the root Dockerfile and starts a local Worker with local R2 storage on port 8787. Keep it running while trying requests in another terminal.

The image uses the Dockerfile's `artifacts` environment to include SBOM generation and artifact verification. The supplied configuration allows `conda-forge`, targets `linux-64` by default, and runs one concurrent solve per container with a persistent solver worker. Containers sleep after ten minutes without activity.

Local testing with the Python adapter and Wrangler 4.131.1 successfully solved `zlib` from conda-forge over HTTPS and retrieved the exact retained output after its native container stopped. The controlled HTTP-channel check below supplies reproducible metadata for integration testing. Hosted channel access still needs a deployment trial.

## Exercise both instances

Ordinary requests choose a member of the pool. The `/_instances/0` and `/_instances/1` prefixes select one explicitly and are removed before forwarding:

```bash
export PRESTO_EDGE_URL=http://127.0.0.1:8787

for instance in 0 1
do
  curl --fail-with-body --silent --show-error \
    --dump-header "instance-${instance}.headers" \
    "$PRESTO_EDGE_URL/_instances/$instance/resolve?spec=zlib&platform=linux-64" \
    --output "instance-${instance}.json"
done
```

Check the JSON results for per-platform errors. `X-Presto-Instance` identifies the selected Durable Object. `X-Presto-Container-Location` appears only when the adapter reads a valid location and matching instance identity inside the native container. It can be absent locally or when observation fails.

The two IDs use `weur` and `enam` Durable Object location hints. These are best effort. Two IDs do not prove two locations, and the Worker's serving location does not establish where a solve ran. See Cloudflare's [placement guidance](https://developers.cloudflare.com/durable-objects/reference/data-location/).

## Retrieve a retained output

A successful response includes `Location: /r/<key>` only after shared publication succeeds. If publication fails, the solve body remains usable without a shared result URL. The `/sbom` endpoint applies the same rule to each document's embedded `location` field.

```bash
result_path=$(awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' instance-0.headers)
test -n "$result_path" &&
  curl --fail --silent --show-error \
    --dump-header retained.headers \
    "$PRESTO_EDGE_URL$result_path" \
    --output retained.json &&
  cmp instance-0.json retained.json
```

`GET` and `HEAD` requests to `/r/<key>` read R2 directly and return `X-Presto-Result-Store: r2`. They do not contact a Presto container. The adapter retains individual outputs up to 64 MiB and refuses reads after its 24-hour expiry. SBOM response envelopes are limited to 8 MiB to bound Worker memory while updating embedded URLs. Oversized or malformed upstream envelopes return HTTP 502.

New solves still use each container's local metadata and request cache. Portable shared solve lookup is not implemented.

## Run the local integration check

Stop any running `uv run pywrangler dev` session first, then run from `deploy/cloudflare`:

```bash
uv run python test/integration.py
```

The check serves a fixed three-package dependency graph under a metadata digest URL. It starts its own local Worker, verifies native solves, lockfile rendering and SBOM generation in both containers, inspects each container's metadata cache and request logs, then stops both containers. It checks exact retained bytes with `GET` and `HEAD`, including the opposite instance selector, and confirms that retrieval does not restart either container. It then verifies two concurrent solves after restarting a stopped container. The JSON report includes observed durations and output hashes. These local measurements do not establish geographic distribution or production performance.

Docker Desktop and OrbStack provide `host.docker.internal` for the test channel. On Linux, supply a reachable host address, for example the Docker bridge gateway:

```bash
uv run python test/integration.py --channel-host "$(docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}')"
```

This check needs no cloud credentials. The channel metadata is fixed and no package archives are downloaded. Wrangler still needs network access to obtain container images and build dependencies.

## Deploy to Cloudflare

Use a Workers Paid account with Containers and R2 available, and credentials permitted to deploy the Worker and manage its resources. Review {doc}`deploy-securely` for the intended audience. Keep Docker running to build the native image.

From `deploy/cloudflare`:

```bash
npx wrangler login
npx wrangler r2 bucket create conda-presto-results
npx wrangler r2 bucket lifecycle add conda-presto-results expire-results results/ --expire-days 1
npx wrangler r2 bucket lifecycle list conda-presto-results
uv run pywrangler deploy
```

Choose different names in `wrangler.jsonc` if needed. Skip bucket creation when using an existing bucket. The lifecycle rule deletes objects under `results/`, so use a dedicated bucket or prefix. R2 deletion is asynchronous. The adapter enforces read expiry independently. See [R2 object lifecycles](https://developers.cloudflare.com/r2/buckets/object-lifecycles/).

Set `PRESTO_EDGE_URL` to the deployed Worker URL and repeat both-instance solves and retrieval checks. Worker activation, image publication and container rollout are separate steps. A completed deploy does not prove the native image is ready. See [Cloudflare deployment behavior](https://developers.cloudflare.com/containers/guides/deploy/).

Record uncached solves, native locations, cold and warm timings, and retrieval after the producing instance stops before claiming the {doc}`edge milestone <../proposals/integration/edge-deployment>` is complete.
