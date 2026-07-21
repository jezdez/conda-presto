# Reference

Detailed specifications for every interface conda-presto exposes.

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`terminal` CLI
:link: cli
:link-type: doc

Subcommand flags, input modes, and exit codes for `conda presto`.
:::

:::{grid-item-card} {octicon}`globe` HTTP API
:link: http-api
:link-type: doc

Endpoint specifications, request/response schemas, and status codes.
:::

:::{grid-item-card} {octicon}`workflow` GitHub Action
:link: github-action
:link-type: doc

Composite action inputs, outputs, and local and remote execution behavior.
:::

:::{grid-item-card} {octicon}`file` Output formats
:link: output-formats
:link-type: doc

JSON, explicit, pixi-lock, conda-lock, environment YAML, and more.
:::

:::{grid-item-card} {octicon}`container` Docker images
:link: docker-images
:link-type: doc

Published image flavors, tags, defaults, and health checks.
:::

:::{grid-item-card} {octicon}`server` Broker service
:link: broker-service
:link-type: doc

Registered service definition, child environment, health check, and commands.
:::

:::{grid-item-card} {octicon}`database` Cache
:link: cache
:link-type: doc

Resolve and solver cache identities, retention, persistence, and refresh rules.
:::

:::{grid-item-card} {octicon}`key` Environment variables
:link: environment-variables
:link-type: doc

Tuning knobs for caching, rate limiting, server behavior, and solver.
:::

:::{grid-item-card} {octicon}`gear` Configuration
:link: configuration
:link-type: doc

Configuration sources, precedence, runtime profiles, and development settings.
:::

:::{grid-item-card} {octicon}`beaker` Presto solver
:link: solver-backend
:link-type: doc

Internal broker-backed `conda --solver=presto` interface and boundaries.
:::

:::{grid-item-card} {octicon}`pulse` Observability
:link: observability
:link-type: doc

Readiness, application logs, refresh counters, and broker diagnostics.
:::

::::

```{toctree}
:hidden:

cli
http-api
github-action
output-formats
docker-images
broker-service
cache
environment-variables
configuration
solver-backend
observability
```
