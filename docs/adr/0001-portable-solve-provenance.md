---
status: accepted
---

# Keep solve provenance portable with shared artifacts

Construction evidence for a shared solve artifact must remain verifiable without contacting the originating Presto service. Distributing the evidence with the artifact preserves that capability after the original cache entry expires, while a Presto retrieval endpoint can provide an additional way to obtain it.

Keep the resolved artifact unchanged in its native format and deliver its solve attestation as a separate companion bundle. Existing consumers can continue to read the artifact directly.

Archived release evidence must support verification without network access. Retain the exact artifact, attestation bundle, profile definition, and recipient-approved trust configuration. Verification records which trust configuration it used. Assessing current vulnerabilities requires separately refreshed evidence.

[ADR 0002](0002-construction-evidence-for-product-security.md) records the agreed assurance and producer trust policy.
