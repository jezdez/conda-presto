# conda-presto

A fast, dry-run conda solver exposed as both a CLI and an HTTP API.
Given package specs or an environment file, it resolves fully pinned
packages for one or more platforms, without downloading or installing
anything, and emits the result as native JSON or any conda exporter
format.

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Quick start
:link: quickstart
:link-type: doc

Install conda-presto and run your first resolve in under a minute.
:::

:::{grid-item-card} {octicon}`mortar-board` Tutorials
:link: tutorials/index
:link-type: doc

Step-by-step guides for the CLI, HTTP API, CI pipelines, and MCP.
:::

:::{grid-item-card} {octicon}`book` Reference
:link: reference/cli
:link-type: doc

CLI flags, endpoint specs, output formats, and environment variables.
:::

:::{grid-item-card} {octicon}`gear` Explanation
:link: explanation/architecture
:link-type: doc

Architecture, performance characteristics, and security model.
:::

:::{grid-item-card} {octicon}`light-bulb` Proposals
:link: proposals/index
:link-type: doc

Design proposals organized by stream: capability, integration, trust.
:::

:::{grid-item-card} {octicon}`log` Changelog
:link: changelog
:link-type: doc

Release history and notable changes.
:::

::::

```{toctree}
:caption: Tutorials
:hidden:

quickstart
tutorials/index
```

```{toctree}
:caption: Reference
:hidden:

reference/cli
reference/http-api
reference/output-formats
reference/environment-variables
reference/configuration
```

```{toctree}
:caption: Explanation
:hidden:

explanation/architecture
explanation/performance
explanation/security
```

```{toctree}
:hidden:

proposals/index
```

```{toctree}
:caption: Project
:hidden:

changelog
```
