# Solve receipts and drift detection

Status: historical proposal, outside the current Presto delivery plan. Historical discussion: [#20](https://github.com/jezdez/conda-presto/issues/20).

The original question was how to explain why a later solve changes when a saved lockfile contains little construction context. Requests, target platforms, virtual packages, solver settings and consumed channel metadata can help explain those differences. Matching repodata hashes alone does not establish that another solve produces identical results.

Presto's possible role is to expose execution facts when an actual consumer requires them, using conda or rattler instrumentation to capture what happened. This does not establish a planned receipt format or drift service.

The current {doc}`verification endpoint <../../reference/http-api>` checks supplied artifacts and signing bundles. It does not compare receipts or detect channel drift.
