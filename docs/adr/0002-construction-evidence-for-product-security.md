---
status: accepted
---

# Provide authenticated solve construction evidence for product security

The intended assurance is a verifiable construction statement from a producer the recipient trusts, bound to the exact solve artifact. It must be usable as supporting evidence in CRA and similar product-security processes, preserving construction context across caching and sharing.

The statement authenticates the producer's reported construction. Independent solver replay, proof of solver correctness, and a conclusion about product conformity require additional evidence and are not established by this statement.

Before introducing a new predicate, evaluate a documented SLSA solve profile against concrete artifacts and verification cases, reusing in-toto and Sigstore. The final attestation schema remains open until that evaluation establishes whether the profile can express and verify the required claims.

The recipient explicitly selects accepted solve producer identities and identity issuers. A hosted solve is attested by its service operator, while a solve executed within CI can be attested by that executing workflow. Running Presto software does not itself make a producer trusted.

Signing disclosure must be explicit, and construction evidence must exclude credentials. The signing configuration must satisfy the producer's confidentiality requirements for private channels and solve requests. Public Sigstore requires a deliberate deployment choice.

The construction statement records exact content digests of the solver index inputs actually consumed, alongside the effective request and solver settings. Historical storage of the full inputs remains a separate capability for consumers that require replay or inspection. Local cache file timestamps and sizes do not establish portable input identity.

The initial implementation covers successful CLI and HTTP solves producing fully resolved artifacts, with every requested platform successful. Serving a cached artifact preserves its original construction evidence. Lockfile conversion and internal transaction provenance remain separate work because they describe different operations.

A request or deployment policy can require attestation. Such a request must produce an artifact with valid construction evidence or fail clearly. It must not silently succeed with unsigned output or incomplete claims when evidence capture or signing is unavailable.

The downstream build or release workflow owns the connection between the attested solve artifact and the shipped product. It records which solve artifact it consumed and what it produced, including any package selection or modification after the solve. Presto's construction statement continues to describe the original solve artifact.

The first worked example uses conda-ship as a separate downstream consumer. It checks artifact compatibility and follows the source solve artifact through package selection into the runtime lock, SBOM, and distributed binary.

Policy work begins with evidence checks linked to relevant CRA and final SSDF requirements. Each check identifies its source requirement, organization-selected parameters, examined evidence, outcome, and missing evidence. The admission service interface and enforcement location remain open until those checks and their consumers are defined.

CRA Article 31 and Annex VII require broader technical documentation covering development, production, vulnerability handling, risks, and tests. The intended contribution of a solve attestation is evidence about dependency resolution within that documentation. The complete set of construction claims and the format of the release workflow's linking record remain to be defined.

Sources: [Regulation (EU) 2024/2847, Article 31 and Annex VII](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/eng), [SLSA provenance](https://slsa.dev/spec/v1.2/build-provenance), and [NIST SSDF 1.1](https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf).
