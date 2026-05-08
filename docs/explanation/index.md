# Explanation

Background and design rationale for how conda-presto works.

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`cpu` Architecture
:link: architecture
:link-type: doc

Process model, caching layers, repodata flow, and how the CLI,
HTTP API, and MCP endpoint share a common solver core.
:::

:::{grid-item-card} {octicon}`zap` Performance
:link: performance
:link-type: doc

Benchmarks, multi-platform parallelism, repodata caching strategy,
and where time is spent in a typical solve.
:::

:::{grid-item-card} {octicon}`shield` Security
:link: security
:link-type: doc

Trust model, rate limiting, request size limits, CORS policy,
and the roadmap toward signed solve attestations.
:::

::::

```{toctree}
:hidden:

architecture
performance
security
```
