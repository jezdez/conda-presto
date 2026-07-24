# Use the browser workbench

This tutorial prepares, resolves, examines, and exports one conda environment
from the first-party browser interface. The workbench does not create or change
an environment.

:::{note}
The workbench is available on current `main`. Use the current-main or source
checkout installation in {doc}`../quickstart` while it remains unreleased.
:::

## Start the server

Start conda-presto in one terminal:

```bash
conda presto --serve
```

Wait until the server is ready:

```bash
curl --fail --silent --show-error http://127.0.0.1:8000/health
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in a browser.

::::{grid} 1 2 4 4
:gutter: 2

:::{grid-item-card} 01 · Prepare
Check specs and file content locally.
:::

:::{grid-item-card} 02 · Resolve
Select packages for each platform.
:::

:::{grid-item-card} 03 · Examine
Inspect versions, builds, and dependencies.
:::

:::{grid-item-card} 04 · Output
Render an installed conda exporter.
:::

::::

## Prepare the request

The initial request contains `python=3.13` and `numpy`. Keep `conda-forge` as
the channel and the displayed native platform as the target.

Add a second `numpy` line, then select **Check input**. The result reports a
`DUP001` warning without running the solver or contacting the channel.

Remove the duplicate and select **Check input** again. The result should report
that the input is ready. Informational findings can remain. They describe
portable input choices rather than solver failures.

## Resolve the environment

Select **Resolve environment**. The workbench submits the same normalized
request used by the JSON API and shows one result section per target platform.

The heading reports the number of selected packages and the observed request
time. When the response can be retained, **Open retained JSON** links to its
content-addressed `/r/<hash>` location.

:::{important}
A retained result is a snapshot. Submit a new resolve request when current
channel metadata matters. The server rechecks repodata before reusing the
mutable resolve cache.
:::

## Examine selected packages

Find `python` in the package table. Compare its version and build with the
original input, then open its dependency count.

Repeat this for `numpy`. The expanded list shows the dependency specifications
recorded in channel metadata. The workbench displays those values as text. It
does not download or inspect package payloads.

## Render exporter output

Change **Output** from **Native package table** to
`environment-yaml`, then select **Resolve environment** again.

The same request is solved through conda's exporter registry. The result now
shows the rendered environment file instead of the package table. Other
installed formats, including lockfile exporters supplied by
`conda-lockfiles`, appear in the same selector.

## Paste an environment file

Open **Paste an environment file**. Keep the filename as `environment.yml` and
paste:

```yaml
channels:
  - conda-forge
dependencies:
  - python=3.13
  - numpy
```

Clear the package-spec field before resolving if you want the file to be the
only input. The filename selects an installed conda environment-specifier
plugin. Package specs left in the main field are added to the parsed file
specs.

## What you learned

- Prepare runs deterministic checks without solving or channel access.
- Resolve uses the same limits, channel policy, cache, and solver as the HTTP API.
- Examine presents package metadata without installing anything.
- Output uses conda's registered exporters rather than a parallel renderer.

Continue with {doc}`http-api` for curl requests or {doc}`review-and-repair` for
repair, diff, and dependency explanations.
