# Attestation retrieval

Status: historical proposal, outside the current Presto delivery plan. Historical discussion: [#22](https://github.com/jezdez/conda-presto/issues/22).

The original question was how a recipient discovers an artifact's signing bundle without each publisher inventing a storage layout. That question belongs to artifact publication and the operator's storage choices. Presto does not need to become an archive or define a retrieval convention to expose signing.

Currently, `/sign` returns a bundle to its caller without automatically persisting an attestation sidecar. Callers save and distribute the artifact and bundle together. Retained `/r/` outputs can expire or be evicted and are not archival storage.

See the {doc}`HTTP API reference <../../reference/http-api>` for signing and the {doc}`cache reference <../../reference/cache>` for retained-output behavior.
