# Compare environments and lockfiles

Status: upstream opportunity, outside Presto's delivery plan. Original discussion: [#14](https://github.com/jezdez/conda-presto/issues/14).

The original proposal addressed reviewing dependency changes without reading a large textual lockfile diff. A package comparison could show additions, removals and changed versions, builds or sources separately for each platform.

The current service resolves inputs and retains eligible outputs, but provides no `/diff` endpoint. Callers can save its outputs for comparison elsewhere. See {doc}`../../reference/output-formats` for the available package fields and {doc}`../../reference/http-api` for retrieval behavior.

Reusable comparison behavior belongs with conda compare. Its current command compares an installed prefix against requirements from an environment file. Comparing two resolved files without solving would require an upstream enhancement.

That proposal should define platforms present on only one side and package identity changes when version and build strings remain equal. Comparing unresolved requirements introduces current channel state and needs distinct behavior. If a consumer needs HTTP access, Presto could supply provider execution, limits and response handling.
