# Monitor the service

Check readiness before sending solves:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
curl --fail --silent --show-error http://127.0.0.1:8000/version
```

A persistent worker that is unavailable produces HTTP 503 while the server replaces it. Readiness does not establish that every channel or the persistent result store is reachable.

For a container, inspect process health and logs:

```bash
docker inspect --format '{{.State.Health.Status}}' conda-presto
docker logs --follow conda-presto
```

For a process deployment, collect stdout and stderr through its supervisor. Keep the default metadata-only access logs and pass `--no-access-log` when invoking uvicorn directly.

Use `/formats`, `/platforms` and `/openapi.json` to inspect advertised capabilities. Exercise an actual small resolve and retained-result retrieval when checking a deployed integration. See {doc}`configure-result-cache`.

HTTP logs contain path, method, content type and status. They omit query parameters, headers and bodies. `CONDA_PRESTO_LOG_LEVEL` controls application verbosity. Logs cover solve/export failures, worker recovery and persistent-store failures. Readiness does not probe Redis, and the service has no metrics or cache-administration endpoint.
