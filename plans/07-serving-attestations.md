# serving-attestations: Serving solve attestations — `<lockfile>.sigs` sidecar

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: [permalink](04-permalink.md), [attestation](06-attestation.md).
Companion plans: [receipt](05-receipt.md), [cep-solve-attestation](14-cep-solve-attestation.md) (CEP draft for the
attestation predicate).
Inspired directly by [conda/ceps#142] (Wolf Vollprecht / prefix.dev)
— "CEP about serving sigstore attestations in Conda repositories".

[conda/ceps#142]: https://github.com/conda/ceps/pull/142

## TL;DR

CEP-27 standardizes the *shape* of an attestation. The draft
[CEP-PR-#142] standardizes how a *channel serves* attestations
*for packages*: a `<package_url>.sigs` sidecar file containing a
JSON array of Sigstore bundles, plus an `attestations` SHA256 in
`repodata.json` for change detection.

We need the **mirror** of that convention for the **solve** world:
how a service that produces *solve* attestations ([attestation](06-attestation.md)) serves
them next to the *lockfile* itself, so that any client — pixi,
conda, `cosign`, `gh attestation verify`, a custom CI script — can
fetch and verify them with the same workflow they already use for
package attestations.

This plan defines that convention for conda-presto **and** stages
it for upstream submission as a follow-on CEP.

## Motivation

- **Closes the missing seam in the trust track.** [receipt](05-receipt.md) produces
  receipts. [attestation](06-attestation.md) produces attestations. [cep-solve-attestation](14-cep-solve-attestation.md) standardizes the
  predicate. Nothing yet says *where to put the attestation*.
  Without that, every consumer reinvents the discovery convention.
- **Reuses an in-flight community decision.** CEP-PR-#142 has
  already done the hard work of deciding *the* idiomatic shape:
  sidecar `.sigs`, JSON array of bundles, repodata hash for change
  detection, `enabled / require / trusted_identities` config.
  Mirroring it costs us almost nothing in design effort and gives
  us "for free" all the client tooling that will be built around
  CEP-PR-#142.
- **One verification UX across the conda supply chain.** A user who
  already configures `enabled / require / trusted_identities` for
  package attestations should be able to use the same config for
  solve attestations.
- **Standardization opportunity.** The conda ecosystem currently
  has no convention for serving solve-time attestations because
  there's no widely-deployed solver service that emits them.
  We're the first; we can set the precedent.

## What CEP-PR-#142 standardizes (recap)

For *packages served by a channel*:

| Element                | CEP-PR-#142 convention                                      |
|------------------------|-------------------------------------------------------------|
| Sidecar URL            | `<package_url>.sigs`                                        |
| Format                 | JSON array of Sigstore bundles                              |
| Empty case (200)       | `[]` returned, not 404                                      |
| 404 meaning            | The package itself does not exist                           |
| Change detection       | `attestations: <sha256>` field added to `repodata.json`     |
| Verification           | Per CEP-27 (in-toto subject matches package digest, etc.)   |
| Client config          | `enabled` / `require` / `trusted_identities`                |
| Status                 | Draft, in active review (Apr 2026)                          |

## What serving-attestations standardizes for solves

The same shape, mapped to the lockfile world:

| Element                | serving-attestations convention                                                 |
|------------------------|--------------------------------------------------------------------|
| Sidecar URL            | `<lockfile_url>.sigs` (alongside any served lockfile)              |
| Format                 | JSON array of Sigstore bundles (CEP-27 / CEP-PR-#142 compatible)   |
| Empty case (200)       | `[]` returned, not 404                                             |
| 404 meaning            | The lockfile itself does not exist                                 |
| Change detection       | `attestations_sha256` field on [permalink](04-permalink.md) metadata           |
| Verification           | Per [cep-solve-attestation](14-cep-solve-attestation.md) predicate; `subject` matches lockfile sha256           |
| Client config          | `enabled` / `require` / `trusted_identities` (mirror CEP-PR-#142)  |
| Subject scope          | One bundle per produced lockfile artifact (per platform)           |

## API surface

### Sidecar emission (next to [permalink](04-permalink.md) permalinks)

When [permalink](04-permalink.md) permalinks are enabled and a solve has an attestation
([attestation](06-attestation.md)), the permalink endpoint also exposes a `.sigs` sidecar:

```
GET /r/<sha256>                  → 200 OK  body: <lockfile>
GET /r/<sha256>.sigs             → 200 OK  body: [<sigstore bundle>, ...]
```

If a permalink exists but no attestation has been produced yet:

```
GET /r/<sha256>.sigs             → 200 OK  body: []
```

If the permalink doesn't exist:

```
GET /r/<sha256>.sigs             → 404 Not Found
```

(Same semantics as CEP-PR-#142.)

### Sidecar emission (inline with `/resolve`)

For solves served directly (no permalink), the attestation is
returned alongside the lockfile (existing [attestation](06-attestation.md) behavior):

```
POST /resolve?attestation=true&format=conda-lock-v1
  → 200 OK
    Content-Type: application/yaml
    X-Solve-Attestation-Bundle: <single base64 bundle>
    body: <lockfile>
```

When [permalink](04-permalink.md) is enabled, the same response also includes a
`Location` (or `Link`) header pointing at the canonical permalink
sidecar, so callers that prefer the standardized fetch path can
use it:

```
Link: </r/<sha256>.sigs>; rel="attestations"
```

### Permalink metadata (the repodata equivalent)

[permalink](04-permalink.md) permalinks already carry metadata. Add an
`attestations_sha256` field that mirrors CEP-PR-#142's
`repodata.attestations`:

```json
GET /r/<sha256>/meta
{
  "lockfile_sha256": "abc...",
  "attestations_sha256": "def...",
  "solved_at": "2026-04-16T18:30:00Z",
  ...
}
```

Mirrors / caches use `attestations_sha256` to detect when the
attestation set for a permalink has changed (e.g. an additional
attestation was added post-hoc via `POST /attest`, [attestation](06-attestation.md)).

### Client configuration (mirroring CEP-PR-#142)

For consumers (pixi, conda, custom tooling) that fetch solve
attestations from a conda-presto instance, the recommended
configuration vocabulary mirrors CEP-PR-#142 verbatim:

```toml
[solve_attestations]
enabled = true
require = "warn"           # error | warn | ignore
trusted_identities = [
  "https://conda-presto.jezdez.dev",
  "https://github.com/myorg/*",
]
```

Same words, same semantics. A user who already understands the
CEP-PR-#142 config understands ours.

### Sidecar format

```json
[
  {
    "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
    "verificationMaterial": { ... },
    "dsseEnvelope": { ... }
  }
]
```

Each element is a valid Sigstore Bundle (v0.2 or v0.3, per
CEP-PR-#142's recommendation). The bundle's enclosed DSSE envelope
contains the in-toto Statement v1 with [cep-solve-attestation](14-cep-solve-attestation.md)'s predicate type
(`https://schemas.conda.org/attestations-solve-1.schema.json`).

## Why a *separate* serving spec for solves (not just CEP-PR-#142)

CEP-PR-#142's serving location (`<package_url>.sigs`) is bound to a
*channel-served package URL*. A solve produces a *lockfile*, not a
package, and the lockfile is served by a *solver service* (or
written to disk by a CLI), not by a channel.

So we need a parallel serving convention with the same *shape* but
a different *anchor*. This is exactly what we're proposing: same
`.sigs` suffix, same JSON array, same HTTP semantics, same client
config — anchored to lockfile URLs instead of package URLs.

A future unified CEP could merge these two conventions under a
single "how to serve any sigstore attestation in conda artifact
hosting" umbrella. We propose to ship the convention first,
upstream the CEP second.

## Composition with existing plans

| Plan          | Composition                                                                 |
|---------------|-----------------------------------------------------------------------------|
| [permalink](04-permalink.md) | Primary anchor: `/r/<sha256>.sigs` is the canonical sidecar URL.         |
| [attestation](06-attestation.md) | Generates the bundles that go into the sidecar.                       |
| [cep-solve-attestation](14-cep-solve-attestation.md) CEP draft | The predicate type used inside the bundles.                             |
| [receipt](05-receipt.md) | Independent. Receipts use a different (HMAC) trust path; no sidecar.       |
| [github-action](09-github-action.md) GH Action | The action's "verify-on-CI" step fetches the sidecar from the upstream solver and verifies per CEP-PR-#142 client workflow. |
| [meta-mcp](10-meta-mcp.md) MCP    | New tool `fetch_solve_attestations(permalink)` returning the sidecar contents. |

## Implementation outline

### 1. New `conda_presto/sidecar.py`

Pure helpers:

```python
def sidecar_path(lockfile_url: str) -> str:
    return lockfile_url + ".sigs"

def sidecar_body(bundles: list[Bundle]) -> bytes:
    return canonical_json([b.to_dict() for b in bundles])

def sidecar_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
```

### 2. Permalink storage extension ([permalink](04-permalink.md) prerequisite)

Permalink storage gains a sidecar slot per stored lockfile. Empty
by default; populated by [attestation](06-attestation.md) when an attestation exists or is
added post-hoc.

### 3. Litestar route additions

```python
@get("/r/{key:str}.sigs")
async def get_sidecar(key: str) -> Response:
    record = permalink_store.get(key)
    if record is None:
        raise NotFoundException
    return Response(
        sidecar_body(record.bundles),
        media_type="application/json",
    )
```

Plus the `attestations_sha256` field on the existing `/r/{key}/meta`
handler.

### 4. Optional `POST /r/{key}.sigs` for adding attestations

For the post-hoc attestation flow (e.g. third-party auditors
attesting an existing permalink), accept a Sigstore bundle and
append it to the sidecar after verifying:

- The bundle's in-toto subject matches the lockfile's sha256
- The bundle's signing identity is permitted by the server's
  upload policy

This mirrors the future-work item facutuesca raised in
CEP-PR-#142 (post-facto attestations from auditors).

### 5. Documentation

Reference CEP-PR-#142 prominently. Document the deliberate parallel.
Provide a verification example using `cosign verify-blob` and
`gh attestation verify` against a real conda-presto sidecar URL.

## Tests

- `GET /r/<sha>.sigs` returns `[]` for a permalink with no attestations
- `GET /r/<sha>.sigs` returns the bundle array after `?attestation=true` solve
- `GET /r/<missing>.sigs` returns 404
- `attestations_sha256` in `/meta` matches `sha256(sidecar body)`
- `attestations_sha256` updates after `POST /r/<sha>.sigs`
- Sidecar body is canonical JSON (stable byte representation across runs)
- `cosign verify-blob` cycle: sign → store → fetch sidecar → verify → success
- `gh attestation verify` cycle: same against a GitHub-OIDC-signed bundle
- Post-hoc upload: bundle with mismatched subject sha256 → 400
- Post-hoc upload: bundle from disallowed identity → 403

## Open questions

- **Q1: Does the sidecar live with the permalink, or also next to
  every served lockfile (including non-permalink solves)?** v1
  proposal: only at the permalink path; non-permalink solves return
  the bundle inline (header) as [attestation](06-attestation.md) specifies. Simplifies
  semantics; permalinks become the canonical place for sigstore
  flows.
- **Q2: Should lockfile *consumers* (e.g. pixi, conda) auto-discover
  the sidecar URL from the lockfile?** Probably yes long-term — a
  lockfile would carry a `attestation_url` field. Out of scope for
  serving-attestations; needs a CEP discussion first.
- **Q3: Bundle version support.** CEP-PR-#142 recommends v0.2 and
  v0.3. Same recommendation for us. Verify against current
  `sigstore-python` defaults.
- **Q4: Upload-policy for post-hoc attestations.** Open / OIDC-bound
  / API-key? v1: OIDC-bound only (matches the "trusted identities"
  worldview); API-key as escape hatch.
- **Q5: HEAD support and Range support on `.sigs`.** CDNs love HEAD;
  cheap to support. Range is silly for ~2-5 KB sidecars; skip.
- **Q6: Cache headers.** Sidecars are immutable as long as the
  permalink hasn't been re-attested. Use strong `ETag` derived from
  `attestations_sha256`; honor `If-None-Match` for cheap polling.
- **Q7: Should we just *send* this as a CEP now?** The serving
  convention for *channels* is still in draft. We could either
  (a) wait until CEP-PR-#142 lands and submit a sibling CEP for
  solves, or (b) submit a unified CEP that covers both. (a) is the
  safer path; (b) is more elegant. Defer the call until CEP-PR-#142
  is closer to acceptance.

## Effort

- Sidecar helpers + storage slot: ~½ day (after [permalink](04-permalink.md) lands)
- `GET /r/<sha>.sigs` + `attestations_sha256` in meta: ~½ day
- `POST /r/<sha>.sigs` post-hoc upload: ~1 day
- Tests + docs + verification examples: ~1 day
- Total: ~3 days *after* [permalink](04-permalink.md) and [attestation](06-attestation.md) land

## Future / out of scope

- **Unified serving CEP.** Once CEP-PR-#142 is accepted, draft a
  follow-up CEP ("Serving sigstore attestations for conda solve
  artifacts") that promotes serving-attestations's convention to a community
  standard. Co-author with CEP-PR-#142 maintainers.
- **Lockfile-embedded attestation URL.** A `attestation_url` field
  in `pixi.lock` / `conda-lock.yml` so consumers don't need to
  reverse-engineer the sidecar location. Needs ecosystem discussion;
  file as a follow-up CEP.
- **Federated attestation registries.** A separate registry that
  collects attestations for any lockfile by sha256, enabling
  cross-instance verification. Genuinely a separate project.

## References

- [CEP-27](https://github.com/conda/ceps/blob/main/cep-0027.md) —
  the attestation schema we conform to.
- [conda/ceps#142](https://github.com/conda/ceps/pull/142) — the
  channel-side serving convention this plan mirrors.
- [permalink](04-permalink.md) — the storage anchor for sidecars.
- [attestation](06-attestation.md) — the producer.
- [cep-solve-attestation](14-cep-solve-attestation.md) CEP draft — the predicate type.
