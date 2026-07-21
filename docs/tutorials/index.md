# Tutorials

Tutorials are guided learning paths. Start with the shortest path that matches
the interface you want to understand.

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} {octicon}`terminal` Create a multi-platform lockfile
:link: cli-resolve
:link-type: doc

Resolve an environment file, inspect native JSON, and export two lockfile
formats.
:::

:::{grid-item-card} {octicon}`globe` Resolve through the HTTP API
:link: http-api
:link-type: doc

Start a local server, submit a solve, follow a result location, and upload a
file.
:::

:::{grid-item-card} {octicon}`checklist` Review and repair an environment
:link: review-and-repair
:link-type: doc

Use preflight, repair, diff, and explain as one review workflow.
:::

:::{grid-item-card} {octicon}`server` Use the local solver service
:link: local-service
:link-type: doc

Start the broker service, call its HTTP API, and run a dry-run conda operation
through the Presto solver.
:::

::::

For a specific operational task, use {doc}`../how-to/index` instead.

```{toctree}
:hidden:

cli-resolve
http-api
review-and-repair
local-service
```
