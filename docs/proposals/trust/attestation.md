# Solve attestations

Status: historical solve-provenance proposal, outside the current Presto delivery plan. Historical discussion: [#21](https://github.com/jezdez/conda-presto/issues/21).

The proposal asked how recipients could connect a lockfile with the request and execution that produced it. Capturing those facts would need an actual consumer and instrumentation in Presto and its solver dependencies. It is not implied by signing an output.

The adapters use Sigstore for signing and conda-sigstore for statement handling and verification. `/sign` signs retained bytes and describes a later signing step. `/verify` checks supplied bytes, bundle, subject name and recipient-selected identity and issuer, with limited signing-step checks. See the {doc}`HTTP API reference <../../reference/http-api>`.

Recipients own trust configuration and decide which evidence they require. Conda-sigstore provides reusable verification outside the service. The existing adapters do not provide solve provenance.
