# Explanation

Explanation pages describe the system's boundaries and the reasons behind its
design. Use {doc}`../reference/index` when you need an exact option or schema.

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`cpu` Architecture
:link: architecture
:link-type: doc

Components, data flow, conda plugin integration, and process ownership.
:::

:::{grid-item-card} {octicon}`container` Deployment models
:link: deployment-models
:link-type: doc

Compare the CLI, ordinary server, Docker worker, broker service, and internal
solver client.
:::

:::{grid-item-card} {octicon}`checklist` Environment review model
:link: environment-review
:link-type: doc

Understand the different evidence supplied by parse, preflight, repair, diff,
explain, resolve, and transcode.
:::

:::{grid-item-card} {octicon}`database` Caching and freshness
:link: caching-and-freshness
:link-type: doc

Repodata, indexes, public responses, private final states, recorded requests,
and scheduled refresh.
:::

:::{grid-item-card} {octicon}`zap` Performance
:link: performance
:link-type: doc

Cold and warm costs, concurrency, cache effects, and meaningful measurement.
:::

:::{grid-item-card} {octicon}`shield` Security and trust
:link: security
:link-type: doc

Solve-only boundaries, local service trust, private channels, and deployment
controls.
:::

::::

```{toctree}
:hidden:

architecture
deployment-models
environment-review
caching-and-freshness
performance
security
```
