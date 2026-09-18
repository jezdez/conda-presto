# A shared solve attestation format

Status: historical proposal, outside the current Presto delivery plan. Historical discussion: [#24](https://github.com/jezdez/conda-presto/issues/24).

The original question was whether independent producers and recipients need shared meanings for solve inputs, execution facts and output artifacts. A CEP would follow demonstrated interoperability needs between actual producers and consumers, rather than being a prerequisite for Presto's service or signing adapters.

No CEP number, predicate identifier or schema is assigned here. There is no planned sequence from construction records through attestations, retrieval and admission to standardization.

The current {doc}`signing API <../../reference/http-api>` records a later output-signing step through conda-sigstore support. It does not implement a standardized solve-provenance format. Any shared format would need to distinguish producer assertions from facts a recipient can independently check.
