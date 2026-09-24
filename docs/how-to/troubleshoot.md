# Troubleshoot the service

See {doc}`monitor-service` for readiness and capability checks.

## The command is missing

Run `conda presto --help` in the environment where conda-presto is installed. Conda and conda-rattler-solver must be available in that same environment. See {doc}`../quickstart` for installation.

## Startup or readiness fails

Check server logs and the configured channels and startup platforms. Persistent workers load their indexes before becoming ready, so channel download failures can prevent startup. Keep startup platforms limited to expected traffic.

For a container:

```bash
docker inspect --format '{{.State.Health.Status}}' conda-presto
docker logs conda-presto
```

The health check follows `CONDA_PRESTO_PORT`. Match the published container port to that setting. A failed persistent worker is replaced by the server, with HTTP 503 during recovery.

## A request is rejected or times out

HTTP 400 indicates invalid input, an unsupported operation, a channel restriction or a request cap. HTTP 413 indicates an oversized body. Native JSON can contain a solver error for one platform, while exporter output requires every requested platform to succeed.

The solve deadline includes waiting for foreground capacity. Metadata inspection temporarily changes conda context and cannot be abandoned safely, so observed latency can exceed the configured deadline if that inspection blocks. Check process resources and logs before increasing timeouts.

## A result has no Location or cannot be retrieved

A valid solve may be returned without retention when credentials are detected, metadata cannot be represented safely, cache capacity is unavailable or a required store write fails. A previously retained URL can expire or be evicted. It is not permanent archival storage.

Use {doc}`configure-result-cache` to check storage configuration and {doc}`../reference/cache` to understand freshness and retention.

## macOS semaphore cleanup warnings

Python may report leaked semaphore objects at process shutdown after a CLI workspace update on macOS. Check the command's exit status and validate the returned lock before using it. The workspace demo checks both and compares all unselected package references. A cleanup warning is separate from the consistency report.
