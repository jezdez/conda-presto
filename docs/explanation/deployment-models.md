# Deployment models

conda-presto exposes one solve engine through several interfaces. The right
interface depends on whether work is one-shot, shared over HTTP, or part of a
local conda transaction.

## Mode comparison

| Mode | Long-lived solve process | Public HTTP API | Internal `/solver/v1` | Scheduled solver-cache refresh | Prefix transaction |
|---|:---:|:---:|:---:|:---:|---|
| `conda presto` CLI | no | no | no | no | none |
| `conda presto --serve` | server only | yes | no | no | none |
| Docker server image | persistent worker | yes | no | no | none |
| `conda-presto.server` broker service | persistent worker | yes, loopback | yes, loopback | yes | none |
| `conda --solver=presto` client | uses broker service | no new listener | client only | through broker service | calling conda process |

The GitHub Action selects either the one-shot CLI or an existing public HTTP
deployment. It does not define another server mode.

## One-shot CLI

The CLI is the smallest execution boundary. It imports conda, parses the input,
solves, writes the result, and exits. It is appropriate for local scripts and CI
jobs where installing conda-presto is simpler than maintaining a service.

Process startup and imports are paid for every invocation. Conda's on-disk
repodata cache can still be reused across commands.

## Ordinary HTTP server

`conda presto --serve` keeps the Litestar application and HTTP result cache
alive. It exposes the public API and performs startup repodata warmup. It does
not enable the private solver route because it is not a broker child.

This mode is useful for development and for deployments that manage uvicorn
directly. Server concurrency, process isolation, and storage are configured
through environment variables.

## Docker server

The server image enables one persistent foreground worker. That worker retains
loaded Python modules, repodata, and solver indexes. The container replaces a
failed worker itself and exposes only the public HTTP API.

The image does not start conda-broker, enable `/solver/v1`, or run scheduled
solver-cache refresh. Adding conda-broker to the installed environment does not
change those boundaries.

## Broker-managed service

conda-broker starts `conda-presto.server` on a broker-assigned loopback port.
The broker supplies the service identity and endpoint environment, monitors the
health command, and restarts the service process after a failure.

This is the only mode that enables the private solver route and scheduled
solver-cache refresh. Loopback reduces network exposure but does not
authenticate other users on the same host, so the service belongs on a trusted
single-user system or inside equivalent process isolation.

## Internal Presto solver client

`conda --solver=presto` is a conda solver plugin, not another server. It captures
the calling conda process's effective solver state, including installed records,
history, pins, settings, and virtual packages. It sends that state to the ready
broker service and receives a final package state.

The caller still computes the unlink and link transaction. Without `--dry-run`,
the caller downloads packages and mutates its prefix locally. The service never
receives the prefix path or file inventory.

The local-only design is intentional. It avoids sending prefix state to a
general remote endpoint and keeps conda's transaction behavior in conda.

Use {doc}`../how-to/run-with-docker` or
{doc}`../how-to/run-broker-service` for deployment steps. Exact boundaries are
listed in {doc}`../reference/docker-images`,
{doc}`../reference/broker-service`, and {doc}`../reference/solver-backend`.
