# Explain package inclusion

Status: upstream opportunity, outside Presto's delivery plan. Original discussion: [#15](https://github.com/jezdez/conda-presto/issues/15).

The original idea was to explain how a requested package brings another package into an environment. This would help users review unexpected dependencies and complement {doc}`package comparison <diff>`.

The current native solve output includes selected package records and their dependency requirements. It does not include requester chains, an `explain` option or a `/explain` endpoint. See {doc}`../../reference/output-formats` for the response fields.

Reusable package graph and query behavior belongs in conda. An upstream proposal should establish which supported interfaces can provide inclusion paths from requested roots through selected records for the same platform. It must handle cycles and multiple paths without producing an unbounded result. If a consumer needs HTTP access, Presto could adapt provider execution, limits and responses.

A dependency path explains why a package is reachable from the request. It does not establish why the solver selected a particular version or rejected another solution. Those questions require different evidence and should not be implied by a package-inclusion explanation.
