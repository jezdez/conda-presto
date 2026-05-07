# Security and trust model

conda-presto resolves packages but does not install them. The trust boundary
sits between "what was solved" and "what gets installed later." The features
described here are proposals for closing that gap. Unless noted otherwise, they
are not yet shipped.

## Solve receipts

A solve receipt is an HMAC-signed blob attached to each solve result. It
captures the inputs (specs, channels, platform) and outputs (resolved packages
with hashes) so that a downstream installer can detect drift between what was
solved and what it is about to install.

## Sigstore attestations

Sigstore attestations provide cryptographic proof of what was solved, by whom,
and when. An attestation binds a solve result to an identity (for example, a CI
service account or a developer's OIDC token) without requiring long-lived
signing keys. This gives teams an auditable chain from "resolve" to "install."

## Sidecar distribution

Attestations and signatures are distributed as `.sigs` files alongside
lockfiles. Keeping them in sidecar files avoids changing the lockfile format
itself, so existing tools can consume the lockfile while security-aware tools
can verify the sidecar.

## Policy engine

The `/admit` endpoint (proposed) provides declarative admission control. A
policy document can constrain:

- Allowed channels
- Allowed or denied packages (by name or pattern)
- License requirements
- Required attestation types

The policy engine evaluates a solve result against the policy and returns an
allow/deny decision before anything is installed.

## CEP alignment

These proposals follow conda enhancement proposals (CEPs) where applicable,
so that signatures, attestations, and policy formats are interoperable across
the conda ecosystem rather than specific to conda-presto.

See the [trust proposals](../proposals/index.md) for detailed designs.
