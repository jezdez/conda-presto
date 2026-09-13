# Deploy the HTTP service

Run conda-presto behind a private ingress or reverse proxy that supplies TLS and caller authentication. The application limits requests and channels but does not authenticate callers.

## Configure admitted work

Set channels and limits before startup:

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

Size these limits for the deployment's capacity. Wildcard channel admission permits caller-selected HTTP and HTTPS URLs, so use it only when the egress policy permits those requests. Set process or container memory and CPU limits as well, since blocked metadata inspection can exceed the configured solve deadline.

Leave CORS unset for server-to-server calls. If a browser client needs the API, set `CONDA_PRESTO_CORS_ORIGINS` to its exact origin. CORS does not replace authentication.

## Start behind the proxy

```bash
uvicorn conda_presto.app:app \
  --host 127.0.0.1 --port 8000 \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 \
  --no-access-log
```

Replace the forwarded address with the actual trusted proxy address. Restrict access to the application listener. The proxy should remove incoming forwarding headers, authenticate callers, enforce body limits, and avoid logging credentials or solve inputs. Correct forwarded addresses matter for per-client rate limits.

For Docker, publish only the intended private host address. See {doc}`run-with-docker`.

## Protect retained results

Use a dedicated service account and cache directory or Redis namespace. Store writers can replace results. File storage needs a hard filesystem quota. Redis needs a memory limit and eviction policy. See {doc}`configure-result-cache`.

Build optional SBOM/signing providers only where needed. Signing requires an explicit operator identity and trust choice. Verify public-signing permission, noninteractive credentials and the recipient's expected identity/issuer independently. See {doc}`../reference/environment-variables`.

## Check the deployment

```bash
curl --fail https://solver.example.org/health
curl --fail https://solver.example.org/version
```

Confirm that the proxy rejects unauthenticated access when required and that disallowed channels fail. Exercise one real solve and retained-result retrieval. `/health` reports worker readiness, not channel or storage health.
