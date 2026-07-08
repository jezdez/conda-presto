# Tutorials

Step-by-step guides to get you started with conda-presto.

::::{grid} 2
:gutter: 3

:::{grid-item-card} {octicon}`terminal` CLI resolve
:link: cli-resolve
:link-type: doc

Resolve package specs and environment files from the command line.
Covers output formats, multi-platform solves, and pipeline workflows.
:::

:::{grid-item-card} {octicon}`globe` HTTP API
:link: http-api
:link-type: doc

Use the HTTP API to resolve environments programmatically.
File uploads, JSON requests, output format conversion, and more.
:::

:::{grid-item-card} {octicon}`play` CI pipeline
:link: ci-pipeline
:link-type: doc

Run conda-presto in GitHub Actions. Local mode installs on the
runner automatically; remote mode calls a hosted deployment.
:::

:::{grid-item-card} {octicon}`browser` Streamlit
:link: streamlit
:link-type: doc

Run a lightweight Streamlit UI backed by the conda-presto HTTP API.
Use Community Cloud's conda dependency support without Pixi.
:::

::::

```{toctree}
:hidden:

cli-resolve
http-api
ci-pipeline
streamlit
```
