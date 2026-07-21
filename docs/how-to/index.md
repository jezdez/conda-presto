# How-to guides

Use these guides when you know what you want to accomplish and need the
commands and configuration to do it.

## Resolve and review

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`terminal` Resolve from the CLI
:link: resolve-from-cli
:link-type: doc

Resolve package specs or environment files and write native JSON or an
exporter format.
:::

:::{grid-item-card} {octicon}`globe` Call the HTTP API
:link: call-http-api
:link-type: doc

Send inline specs, JSON bodies, or raw environment files to a running server.
:::

:::{grid-item-card} {octicon}`checklist` Inspect environments
:link: inspect-environments
:link-type: doc

Validate, repair, compare, and explain proposed environments.
:::

:::{grid-item-card} {octicon}`arrow-switch` Transcode lockfiles
:link: transcode-lockfiles
:link-type: doc

Convert a covered lockfile to another lockfile format without solving.
:::

::::

## Run and integrate

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`container` Run with Docker
:link: run-with-docker
:link-type: doc

Run the published HTTP server or one-shot CLI image.
:::

:::{grid-item-card} {octicon}`server` Run the broker service
:link: run-broker-service
:link-type: doc

Start and manage the loopback-only service registered with conda-broker.
:::

:::{grid-item-card} {octicon}`beaker` Use the Presto solver
:link: use-presto-solver
:link-type: doc

Delegate a conda final-state solve to the local broker service.
:::

:::{grid-item-card} {octicon}`play` Use the GitHub Action
:link: use-github-action
:link-type: doc

Resolve an environment locally or through a hosted API in CI.
:::

::::

## Operate the service

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`database` Configure result caching
:link: configure-result-cache
:link-type: doc

Choose a memory, file, or Redis result store and verify public cache entries.
:::

:::{grid-item-card} {octicon}`sync` Refresh solver results
:link: refresh-solver-cache
:link-type: doc

Keep recorded final-state solves current in the broker-managed service.
:::

:::{grid-item-card} {octicon}`pulse` Monitor the service
:link: monitor-service
:link-type: doc

Use readiness, broker state, service logs, and capability endpoints.
:::

:::{grid-item-card} {octicon}`tools` Troubleshoot
:link: troubleshoot
:link-type: doc

Diagnose service discovery, readiness, solver, cache, and deployment problems.
:::

:::{grid-item-card} {octicon}`shield` Deploy securely
:link: deploy-securely
:link-type: doc

Configure the network boundary, trusted proxy, request limits, and cache
isolation for a production HTTP API.
:::

:::{grid-item-card} {octicon}`pulse` Benchmark performance
:link: benchmark-performance
:link-type: doc

Compare controlled result-cache, process, and persistent-index profiles.
:::

::::

```{toctree}
:hidden:

resolve-from-cli
call-http-api
inspect-environments
transcode-lockfiles
run-with-docker
run-broker-service
use-presto-solver
configure-result-cache
refresh-solver-cache
monitor-service
troubleshoot
use-github-action
deploy-securely
benchmark-performance
```
