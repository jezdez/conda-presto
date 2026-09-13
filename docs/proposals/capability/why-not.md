# Explain failed solves and suggest repairs

Status: upstream opportunity, outside Presto's delivery plan. Original discussion: [#13](https://github.com/jezdez/conda-presto/issues/13).

The original proposal aimed to answer why requested packages cannot be installed together. Useful diagnostics would identify conflicting requirements and help a user decide which request to change.

The current service returns solver failures through its existing native JSON and exporter error paths. The earlier `/repair` operation was removed. There is no current `/why-not` endpoint or repair-suggestion API. See {doc}`../../reference/output-formats` for per-platform failure behavior.

Reusable failure explanations and repair search belong with conda and its solver providers, with doctor integration where appropriate. Prospective-request diagnosis would require upstream support beyond doctor's current installed-prefix checks. A readable failure message, a conflict graph and a minimal conflicting set are different results. Each needs evidence from the provider.

Repairs should be checked against the complete original request on every requested platform and remain advice the caller chooses to apply. If a consumer needs HTTP access, Presto could adapt provider execution, deadlines, request limits and partial responses. Successful dependency explanations belong in {doc}`explain` and need not depend on repair search.
