# Troubleshoot conda-presto

Start with the symptom that matches the failing workflow. Keep broker, Docker,
and ordinary HTTP deployments separate because they have different lifecycle
and solver capabilities.

## The conda commands are missing

Verify that conda-presto is installed in the active conda environment:

```bash
conda presto --help
conda broker list
```

If `conda presto` works but `conda-presto.server` is absent from the broker
list, check provider discovery:

```bash
conda broker doctor
conda broker doctor --json
```

Do not assume that a package installed in another conda environment is visible
to the current plugin registry.

## The broker service does not become ready

An empty repodata cache can make the first startup slow. Wait with the service
health-check start period in mind:

```bash
conda broker wait conda-presto.server --timeout 180
```

Then inspect process state, lifecycle events, and logs:

```bash
conda broker status conda-presto.server --json
conda broker events --lines 100
conda broker logs conda-presto.server --lines 200
```

Check channel reachability, credentials, repodata cache permissions, and the
configured channels and platforms. A persistent worker that fails or times out
causes `/health` to return 503 until conda-broker replaces the service process.

## The Presto solver reports no ready service

Start and wait for the exact registered service:

```bash
conda broker start conda-presto.server
conda broker wait conda-presto.server --timeout 180
conda broker endpoint conda-presto.server
```

The solver accepts only a ready loopback endpoint supplied by conda-broker.
There is no remote solver URL override.

## A solver operation is rejected before a request

The internal backend rejects offline solves, `--update-deps`, and conda-build
caller-provided indexes. Use another conda solver for those operations.

See {doc}`use-presto-solver` for supported command shapes and
{doc}`../reference/solver-backend` for the complete boundary.

## A repeated solver request is not clearly faster

A final-state hit still performs broker discovery, HTTP encoding and decoding,
and repodata freshness checks. It also requires the solve-affecting state to
match, including installed records, history, pins, virtual packages, requested
specs, channels, and solver settings.

A completed package transaction usually changes the installed state and the
next cache identity. Timing alone cannot identify a hit because no per-request
cache status is exposed.

## A resolve response has no Location header

The result was returned but not retained. Check:

- `CONDA_PRESTO_RESULT_CACHE_SIZE`
- `CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB`
- persistent-store connection and permissions
- service logs for read, write, or metadata warnings
- whether repodata markers changed while the solve was running

See {doc}`configure-result-cache` for a retrieval check.

## A cached result is not reused after repodata changes

This is expected. `/resolve` cache identity includes local repodata cache-file
markers. The solver final-state cache applies the caller's effective repodata
policy and requires current markers to match the stored entry.

Different dependency versions or solve-affecting request fields also select a
different entry.

## Scheduled refresh does not run

Check every prerequisite:

- the process is the `conda-presto.server` broker child
- persistent-worker mode is enabled by the provider
- `CONDA_PRESTO_SOLVER_CACHE_WARM_INTERVAL_S` is greater than zero
- `CONDA_PRESTO_SOLVER_CACHE_WARM_BATCH_SIZE` is greater than zero
- `CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_SIZE` is greater than zero
- the result cache has in-process capacity or a persistent backend
- the same successful cacheable solver request has been used at least twice
- at least 30 seconds have elapsed since readiness for the first cycle

If variables were exported after the broker daemon started, stop the broker
and start it again. Restarting only the service retains the daemon's old
environment.

Use {doc}`refresh-solver-cache` for the complete setup.

## Persistent candidate startup fails

`CONDA_PRESTO_SOLVER_CACHE_WARM_CANDIDATE_PERSIST=true` requires a file or
Redis result-cache backend. Configure that backend first.

Credential-bearing requests are intentionally excluded from persistence. They
remain memory-only even when persistence is enabled.

## A file cache fails in Docker

The image runs as UID and GID 10001. Ensure the mounted cache directory is
writable by that identity. Follow the volume initialization in
{doc}`run-with-docker`.

## Docker cannot use the Presto solver or scheduled refresh

This is expected. The Docker server has a persistent foreground worker but no
conda-broker child identity. It omits `/solver/v1` and does not run scheduled
solver-result refresh.

Use the broker-managed local service for those features.

## The server rejects a requested channel

Add the channel to `CONDA_PRESTO_ALLOWED_CHANNELS` and restart the server. Keep
the allowlist narrow for deployments that accept untrusted requests.

## Clients receive HTTP 429 behind a proxy

Either reduce request volume or adjust `CONDA_PRESTO_RATE_LIMIT`. When a
reverse proxy terminates client connections, configure uvicorn's
`--forwarded-allow-ips` so rate limiting uses the intended client address.

## A raw file upload is rejected

Match the `Content-Type` to the input and pass `filename` when the type is
ambiguous. Do not wrap a raw upload in JSON unless using the JSON request shape.

Accepted media types and error statuses are listed in
{doc}`../reference/http-api`.

For the exact cache and monitoring contracts, see {doc}`../reference/cache`
and {doc}`../reference/observability`.
