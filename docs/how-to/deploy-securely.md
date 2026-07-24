# Deploy the HTTP API securely

Put the public conda-presto API behind a network boundary that supplies TLS and
access control. The application provides request limits, channel admission,
CORS, and per-client rate limiting. It does not authenticate callers.

:::{warning}
Do not expose the broker-managed service. It is a loopback service for local
clients and includes the private `/solver/v1` route. Use an ordinary or Docker
HTTP server for a network deployment.
:::

## Choose the admission boundary

Use one of these patterns:

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} Host-local API
Bind conda-presto to `127.0.0.1` and allow only processes on the same trusted
host to connect.
:::

:::{grid-item-card} Protected network API
Place conda-presto behind a reverse proxy or private ingress that terminates
TLS, authenticates callers, and restricts source networks.
:::

::::

Publishing the service on all interfaces without an admission layer makes its
solve capacity and configured channel access available to the network.

## Restrict channels and request work

Set an explicit channel allowlist and limits before the process starts. Adjust
the example values to the largest requests the deployment intends to support:

```bash
export CONDA_PRESTO_CHANNELS=conda-forge
export CONDA_PRESTO_ALLOWED_CHANNELS=conda-forge
export CONDA_PRESTO_RATE_LIMIT=300
export CONDA_PRESTO_MAX_BODY_BYTES=1048576
export CONDA_PRESTO_MAX_SPECS=200
export CONDA_PRESTO_MAX_CHANNELS=8
export CONDA_PRESTO_MAX_PLATFORMS=8
export CONDA_PRESTO_SOLVE_TIMEOUT_S=60
export CONDA_PRESTO_PARSE_TIMEOUT_S=10
```

Avoid `CONDA_PRESTO_ALLOWED_CHANNELS=*` unless the surrounding network and
egress policy deliberately allow callers to select arbitrary HTTP and HTTPS
channel URLs. The wildcard does not admit `file://` paths. List a required
local channel explicitly and expose it only to a trusted deployment.

Set browser origins only when a browser application needs the API:

```bash
export CONDA_PRESTO_CORS_ORIGINS=https://solver.example.org
```

Leave `CONDA_PRESTO_CORS_ORIGINS` unset for server-to-server deployments. CORS
does not replace proxy authentication.

## Isolate persistent results

Use a cache backend owned by this deployment's service account and trust
domain. For a local file store:

```bash
mkdir -p "$HOME/.cache/conda-presto/results"
chmod 700 "$HOME/.cache/conda-presto/results"
export CONDA_PRESTO_RESULT_CACHE_BACKEND=file
export CONDA_PRESTO_RESULT_CACHE_DIR="$HOME/.cache/conda-presto/results"
```

Do not share a file directory or Redis namespace between deployments that have
different channel credentials or caller populations. Avoid a shared public
deployment for private-channel solves.

Put a file cache on a filesystem or volume with a hard quota. Configure a
dedicated Redis instance with `maxmemory` and a cache eviction policy such as
`allkeys-lru`. Entry expiry and per-value checks do not cap aggregate storage,
and a Redis namespace does not isolate memory or eviction policy.

See {doc}`configure-result-cache` for Redis and Docker volume configuration.

## Start behind a reverse proxy

Bind the application to a private listener and tell uvicorn exactly which
proxy addresses may supply forwarded client information:

```bash
uvicorn conda_presto.app:app \
  --host 127.0.0.1 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips 127.0.0.1 \
  --no-access-log
```

Replace `127.0.0.1` in `--forwarded-allow-ips` with the exact proxy address or
trusted proxy network when the proxy runs elsewhere. Do not use `*` unless
untrusted clients cannot reach the application listener directly.

Configure the reverse proxy or ingress to:

- terminate TLS
- authenticate callers when the API is not public
- remove untrusted incoming forwarding headers
- set the client address and original scheme forwarding headers
- enforce a request-body limit at least as strict as the application limit
- proxy only to the private application listener
- restrict its own access logs to fields appropriate for the deployment

The trusted client address matters because Litestar applies
`CONDA_PRESTO_RATE_LIMIT` per client IP.

:::{note}
The `conda presto --serve` convenience command disables uvicorn's access log,
but it does not expose uvicorn's forwarded-address option. Invoke uvicorn
directly with `--no-access-log` when the deployment relies on proxy-provided
client addresses. Litestar continues to log the public request path, method,
content type, and response status while excluding `/solver/v1`.
:::

## Verify the deployed boundary

Check readiness and version information through the externally protected URL:

```bash
curl --fail --silent --show-error https://solver.example.org/health
curl --fail --silent --show-error https://solver.example.org/version
```

Then verify from an unauthenticated client that the proxy denies access when
authentication is required. Confirm that a request outside the configured
channel allowlist fails before enabling production traffic.

## Go-live checklist

- [ ] The application listener is reachable only through the intended network boundary.
- [ ] TLS terminates at a trusted proxy or ingress.
- [ ] Authentication is enabled when the API is not intentionally public.
- [ ] `CONDA_PRESTO_ALLOWED_CHANNELS` names only approved channels.
- [ ] Request size, count, concurrency, and timeout settings match available capacity, with process or container limits covering blocked metadata inspection.
- [ ] Uvicorn trusts forwarded headers only from known proxy addresses.
- [ ] CORS is unset or contains only required browser origins.
- [ ] Cache files or Redis keys are isolated to this deployment's trust domain, with a hard filesystem quota or Redis memory and eviction limit.
- [ ] Uvicorn access logging is disabled and reverse-proxy logs omit sensitive fields.
- [ ] `/health` is used for readiness without treating it as a channel or cache health probe.

Read {doc}`../explanation/security` for the trust model and
{doc}`../reference/environment-variables` for every setting and default. For a
container deployment, also follow {doc}`run-with-docker` and keep the published
host port private to the proxy or local host.
