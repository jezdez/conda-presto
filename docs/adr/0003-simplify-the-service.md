---
status: accepted
---

# Return to conda operations as a service

Keep solving, parsing, registered exporters, restricted lockfile conversion, retained outputs and the worker controls needed to run them over HTTP. Keep a compact CLI, one server image and an Action that calls an explicit endpoint.

Remove separate diagnostics, the browser, local transaction delegation and scheduled request replay. This is an alpha scope reset. It requires no migration machinery or replacement projects.

Add optional adapters for conda-sboms and conda-sigstore. SBOM generation describes resolved records. Output signing authenticates a retained artifact with the service's identity. Verification binds supplied bytes to a statement and the recipient's expected signer identity and issuer.

Defer the full solve construction evidence and framework-linked release checks in issues 20–24. ADRs [0001](0001-portable-solve-provenance.md) and [0002](0002-construction-evidence-for-product-security.md) preserve the accepted assurance requirements. Generic artifact signing does not satisfy them, and deferral does not weaken them.

Future additions need a concrete integration problem. A new endpoint should normally adapt an operation owned by an existing conda provider.
