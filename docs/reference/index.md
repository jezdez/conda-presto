# Reference

Detailed specifications for every interface conda-presto exposes.

::::{grid} 2
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

:::{grid-item-card} {octicon}`file` Output formats
:link: output-formats
:link-type: doc

JSON, explicit, pixi-lock, conda-lock, environment YAML, and more.
:::

:::{grid-item-card} {octicon}`key` Environment variables
:link: environment-variables
:link-type: doc

Tuning knobs for caching, rate limiting, server behaviour, and solver.
:::

:::{grid-item-card} {octicon}`gear` Configuration
:link: configuration
:link-type: doc

`.condarc` settings, channel priority, and solver options.
:::

::::

```{toctree}
:hidden:

cli
http-api
output-formats
environment-variables
configuration
```
