# attestation: Sigstore solve attestations (CEP-27 aligned)

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: [receipt](receipt.md) — shares channel-snapshot capture and
the underlying data structure.
Companion plans: [cep-solve-attestation](cep-solve-attestation.md) (the CEP draft that registers the new
predicate type upstream), [serving-attestations](serving-attestations.md) (the `.sigs` sidecar serving
convention that mirrors CEP-PR-#142 for solve attestations),
[admit](admit.md) (admission engine that consumes these attestations).

## TL;DR

Emit conda-presto solve results as **publicly verifiable
sigstore-signed in-toto attestations**, fully aligned with the
existing CEP-27 supply-chain stack. Anyone, anywhere can verify a
conda-presto attestation against the public Rekor transparency log
using `cosign`, `gh attestation verify`, or `sigstore-python` —
without trusting our server, our keys, or our HMAC secret.

The attestation declares: "this resolved environment was produced by
this conda-presto identity from these inputs against this channel
snapshot." It is the public, portable, third-party-verifiable
counterpart to [receipt](receipt.md)'s local receipt.

## Motivation

CEP-27 ([accepted, Feb 2025](https://github.com/conda/ceps/blob/main/cep-0027.md)),
authored by Wolf Vollprecht (prefix.dev) and William Woodruff (Trail
of Bits), already standardized:

- Attestation format: in-toto Statement v1
- Envelope: DSSE
- Signing: Sigstore (keyless via OIDC-issued ephemeral certs from
  Fulcio)
- Distribution: Sigstore bundle
- Verification: `cosign` / `sigstore-python` / `gh attestation`
- Reference impl: `rattler-build --generate-attestation` +
  `actions/attest@v1`
- Reference repo: https://github.com/prefix-dev/sigstore-example
- Live deployment: prefix.dev (public beta)

CEP-27 explicitly invites additional predicate types as future work:

> *Future iterations of conda's attestation design may wish to
> support and use other predicate types, such as the SLSA Provenance
> layout. Doing so would expose additional metadata about the
> package's source and build provenance, giving conda package
> consumers greater control over their consumption and admission
> policies.*

CEP-27's predicate (`attestations-publish-1`) is about **package
publishing** ("Alice published numpy-1.24.3 to conda-forge").
conda-presto's natural predicate is about **environment solving**
("conda-presto solved this lockfile from these specs against these
channels at this time"). These are orthogonal layers; neither
replaces the other.

This makes conda-presto the obvious project to define and ship the
**solve attestation** predicate, in the same spirit and stack as
CEP-27.

## What it gives us beyond [receipt](receipt.md)

| Capability                            | [receipt](receipt.md) | attestation attestation |
|---------------------------------------|:--------------:|:-------------------:|
| Drift detection on the issuing instance | ✓            | ✓                   |
| Verifiable without trusting the issuer  |              | ✓                   |
| Verifiable with no shared secret        |              | ✓                   |
| Cross-organization trust establishment  |              | ✓                   |
| Public transparency log entry           |              | ✓ (Rekor)           |
| Verifiable years from now               | only with same secret | ✓                |
| Tooling already in users' hands         |              | ✓ (cosign, gh)      |
| SLSA L3-track credibility               |              | ✓                   |
| EU CRA / NIST SSDF compliance story     |              | ✓                   |
| Wire cost                               | ~200 B        | ~2-5 KB             |
| Latency cost                            | µs            | ~100-500 ms (Rekor) |

## API surface

### Attestation emission

```
POST /resolve?spec=numpy&format=conda-lock-v1&attestation=true
  → 200 OK
    Content-Type: application/yaml
    X-Solve-Attestation-Bundle: <base64 sigstore bundle>
    body: <conda-lock.yml>
```

For JSON responses (no `?format=`), include the attestation as a
sibling field in the response body:

```text
{
  "results": [...],
  "attestation_bundle": "<base64 sigstore bundle>"
}
```

`?attestation=true` is opt-in. Setting both `?receipt=true` and
`?attestation=true` is supported and returns both — they describe
the same underlying solve and don't conflict.

### Standalone attestation endpoint (post-hoc)

For solves cached via [permalink](../integration/permalink.md) permalinks, allow attesting after the
fact:

```
POST /attest
  Content-Type: application/json
  { "permalink": "/r/3a7f...e91b" }
  → 200 OK
  { "attestation_bundle": "<base64>" }
```

Useful when a caller wants to add an attestation to a solve they
already have a receipt for, or when a CI system wants to attest only
on release-tag builds.

### Verification

Standard sigstore tooling. No conda-presto-specific verifier needed:

```bash
# Using cosign (works against the Public Good Sigstore instance)
cosign verify-blob env.lock \
  --bundle env.lock.attestation \
  --certificate-identity-regexp 'https://conda-presto\..*' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# Using gh CLI (when signed by GitHub Actions identity)
gh attestation verify env.lock --owner conda-presto

# Using sigstore-python
python -m sigstore verify identity \
  --bundle env.lock.attestation \
  --cert-identity 'https://conda-presto.jezdez.dev' \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  env.lock
```

Optional: ship a small `conda presto verify-attestation` CLI that
wraps `sigstore-python` with conda-presto-specific predicate
validation (checks `predicateType` matches our schema, prints the
solve inputs in a readable form, etc.). Convenience only.

## Predicate shape (proposed; full spec in [cep-solve-attestation](cep-solve-attestation.md))

```json5
{
  "_type": "https://in-toto.io/Statement/v1",
  "predicateType": "https://schemas.conda.org/attestations-solve-1.schema.json",
  "subject": [
    {
      "name": "environment.lock",
      "digest": { "sha256": "<sha256 of the lockfile body>" }
    }
  ],
  "predicate": {
    "solver": {
      "name": "rattler",
      "version": "0.X.Y"
    },
    "builder": {
      "id": "https://conda-presto.jezdez.dev",
      "version": "0.X.Y"
    },
    "request": {
      "specs": ["numpy", "scipy"],
      "channels": ["conda-forge"],
      "platforms": ["linux-64"],
      "format": "conda-lock-v1",
      "request_hash": "3a7f..."
    },
    "channelSnapshot": [
      {
        "url": "https://conda.anaconda.org/conda-forge",
        "subdir": "linux-64",
        "repodataSha256": "8e2f...",
        "fetchedAt": "2026-04-16T18:30:00Z"
      }
    ],
    "solvedAt": "2026-04-16T18:30:01Z",
    "publishAttestations": [
      {
        "package": "numpy-1.26.4-py312h7f...0.conda",
        "sha256": "abcd...",
        "rekorUuid": "108e9186e8c5677a..."
      }
    ]
  }
}
```

The `publishAttestations` field is the **chain-of-trust feature**:
for each resolved package whose CEP-27 publish attestation can be
located on the Public Good Rekor instance, the solve attestation
records its UUID. A verifier can then walk the chain: "the lockfile
was solved by *X*, contains package *Y*, which was published by *Z*."
This is genuinely new in the conda ecosystem.

For v1 the field is optional (skip when no publish attestation can
be located, common during the CEP-27 rollout phase). It becomes more
populated over time as conda-forge attestation coverage grows.

## Implementation outline

### 1. Reuse [receipt](receipt.md)'s channel-snapshot capture

The `Receipt` dataclass and the `repodata_sha256` capture in
`build_index` are shared. Both `Receipt.encode()` and a new
`Attestation.build(...)` consume the same in-memory data; they only
differ in encoding/signing.

### 2. New `conda_presto/attestation.py` module

```python
from sigstore.sign import Signer
from sigstore.models import Bundle

def build_statement(receipt: Receipt, lockfile_sha256: str,
                    publish_attestations: list[PublishRef]) -> dict:
    # produces the in-toto Statement above

def sign_with_sigstore(statement: dict, signer: Signer) -> Bundle:
    # canonicalize → DSSE envelope → sigstore sign → bundle
```

`sigstore-python >= 4.0` is the recommended dependency (current
stable: v4.2.0). Same library prefix.dev's `rattler-build` uses for
CEP-27 attestations.

### 3. Three signing modes (signer abstraction)

| Mode               | Identity source                    | Suitable for                |
|--------------------|------------------------------------|------------------------------|
| `dev` (default off) | Local ed25519 key file              | Local development, tests   |
| `oidc-ambient`     | GitHub Actions / GCP / AWS / Azure ambient OIDC token | Hosted deployments running in OIDC-aware environments |
| `oidc-interactive` | Browser-based OIDC flow             | Manual signing from a workstation |

Configured via `CONDA_PRESTO_ATTESTATION_SIGNER` env var. Default
`off` (no attestations emitted; `?attestation=true` returns
`501 Not Implemented` with a configuration hint).

The public deployment runs a long-lived GitHub Actions workflow
(or a cloud machine identity) so `oidc-ambient` works out of the box.

### 4. New `POST /attest` handler

Looks up a permalink ([permalink](../integration/permalink.md)) → loads the cached solve and its
captured snapshot data → builds and signs an attestation → returns
the bundle. Re-attestation of the same solve is idempotent.

### 5. Optional Rekor publication

`?log=true` on `?attestation=true` (default true once the public
deployment is fully OIDC-configured). Without `log=true`, the
attestation is signed but not logged — useful for ephemeral test
flows.

### 6. Optional `verify-attestation` CLI command

```
conda presto verify-attestation env.lock --bundle env.lock.bundle \
  --identity 'https://conda-presto.jezdez.dev'
```

Thin wrapper around `sigstore-python` with conda-specific predicate
validation. Optional; users can also use `cosign` directly.

## Tests

- Build statement from receipt → in-toto schema validates
- Sign with dev key → bundle round-trips through sigstore-python verify
- Sign with mock OIDC token → bundle includes correct identity claim
- Predicate `request_hash` matches independent recomputation
- `publishAttestations` lookup: stub Rekor → correct UUIDs surfaced
- `publishAttestations` graceful degradation: stub Rekor returns no
  match → field omitted, no error
- `?attestation=true` without configured signer → 501 with hint
- `POST /attest` against a permalink → returns the same bundle as
  the original solve would have
- `POST /attest` against a non-existent permalink → 404
- Multi-platform solve → one attestation per platform, or one
  attestation with multiple subjects (decision: file in [cep-solve-attestation](cep-solve-attestation.md))
- Compatibility: bundle verifies cleanly with `cosign verify-blob`
  and `gh attestation verify`

## Open questions

- **Q1: One attestation per (lockfile, platform), or one per solve
  with N subjects?** in-toto allows N subjects per Statement. For
  multi-platform conda-presto solves we have N lockfiles. Cleaner
  to emit one attestation per subject (per-platform), but more
  bundles to distribute. Tentatively: per-platform; revisit in [cep-solve-attestation](cep-solve-attestation.md).
- **Q2: Where does the Rekor publication latency budget come from?**
  ~100-500 ms is meaningful relative to a fast solve. Acceptable
  because attestation is opt-in. Document the latency in OpenAPI;
  consider an async "attest-after" mode for clients that don't want
  to block.
- **Q3: Should we sign the entire bundle ourselves OR delegate to
  the GitHub Actions `actions/attest@v1` action?** Both work.
  In-process signing via `sigstore-python` keeps the API
  self-contained; delegating to `actions/attest` is the path of
  least friction in CI but doesn't help for non-CI deployments.
  Recommendation: support in-process signing first, document the
  `actions/attest` integration as an alternative for CI-only
  setups.
- **Q4: Bundle distribution alongside the lockfile.** The Sigstore
  Bundle JSON is ~2-5 KB. Returning it as a header is borderline
  (many proxies cap header size at 8 KB total). Returning it in the
  body next to the lockfile is cleaner for JSON responses; for
  lockfile responses, consider a multipart response or a `Link:`
  header pointing at a `/attestations/<sha>` GET. Tentatively:
  header for v1, switch to multipart if user reports header-size
  issues.
- **Q5: Trust roots and identity policies.** Verifiers need to know
  which identities are valid signers. We should publish a small
  `trust-policy.json` that lists the canonical conda-presto
  identities (the public deployment's GitHub identity, etc.) so
  client tooling can configure verification cleanly.
- **Q6: Deferred predicate fields.** Worth including
  `slsa.buildType` and other SLSA-flavored fields in our predicate
  for forward compatibility with SLSA verifiers, even though we
  define a conda-specific predicate? Discuss in [cep-solve-attestation](cep-solve-attestation.md).

## Effort

- v1 in-process signing + `?attestation=true` (no Rekor): ~3 days
- Rekor publication + bundle distribution: ~1 day
- `publishAttestations` lookup against Rekor: ~1 day
- `POST /attest` for permalinks: ~½ day
- Optional `verify-attestation` CLI: ~½ day
- Tests + docs: ~2 days
- Total: ~1-1.5 weeks, plus [receipt](receipt.md) as prerequisite

Best landed AFTER [receipt](receipt.md) (which provides the channel-snapshot
infrastructure) and AFTER or alongside [cep-solve-attestation](cep-solve-attestation.md) (which formalizes
the predicate). [github-action](../integration/github-action.md) (GitHub Action) lands after attestation so
"verify-on-CI" can be a one-line action default.

## Out of scope (file as follow-ups)

- **TUF integration.** Trust root distribution via The Update
  Framework. Possible later; CEP-27 leaves this as future work too.
- **Per-package verification at install time.** Conda-presto
  attests the solve; conda (the installer) would need to chain that
  with package-level CEP-27 verification at install. That's a
  conda-side change, not ours.
- **Verification Summary Attestation (VSA) emission.** When
  `/verify` succeeds, optionally emit a sigstore-signed VSA
  recording the verification event. Useful for audit trails.
  File as a small follow-up.
- **Time-stamped attestations.** RFC 3161 timestamping for
  non-Rekor attestation modes. Niche; defer.

## References

- [CEP-27](https://github.com/conda/ceps/blob/main/cep-0027.md) — the
  publish attestation precedent and the schema-design pattern we
  mirror.
- [prefix-dev/sigstore-example](https://github.com/prefix-dev/sigstore-example)
  — reference implementation for CEP-27 in `rattler-build`.
- [sigstore-python](https://github.com/sigstore/sigstore-python) —
  signing library; same one `rattler-build` uses.
- [in-toto Statement v1](https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md)
- [DSSE envelope](https://github.com/secure-systems-lab/dsse/blob/master/envelope.md)
- [Sigstore bundle](https://docs.sigstore.dev/about/bundle/)
- [PEP 740](https://peps.python.org/pep-0740/) — PyPI's parallel
  attestation scheme, by the same Trail of Bits author.
- [OpenSSF TAC funding request #472](https://github.com/ossf/tac/issues/472)
  — broader sigstore-for-conda governance context.
