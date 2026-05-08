# receipt: Solve receipts and `/verify` — local drift detection

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Last updated: 2026-04-16 (renamed witness→receipt to avoid CEP-27 terminology overlap)

Depends on: nothing strictly. Composes with [transcoder](../capability/transcoder.md), [diff](../capability/diff.md), [permalink](../integration/permalink.md), [meta-mcp](../integration/meta-mcp.md).
Companion plans: [attestation](attestation.md) (sigstore attestations, CEP-27 aligned),
[cep-solve-attestation](cep-solve-attestation.md) (CEP draft for solve attestation predicate).

## TL;DR

Add a fast, local drift-detection primitive to conda-presto:

1. **Every solve can emit a "receipt"** — a small HMAC-signed record
   that captures the channel state, solver version, and request shape
   that produced the result.
2. **A new `POST /verify` endpoint** — given a lockfile + a receipt,
   confirm "yes, this lockfile is exactly what you'd get if you
   re-solved against the same channel state today; nothing has drifted."

This addresses the **lockfile-rot problem**: lockfiles have no proof
they were ever produced cleanly, and no way to assert what channel
snapshot they were made against.

## Receipt vs. attestation — vocabulary

To stay out of the way of the conda ecosystem's emerging supply-chain
vocabulary (CEP-27 etc.), we deliberately call this a **receipt**, not
a witness, attestation, or provenance statement.

|                | Receipt                         | Attestation ([attestation](attestation.md))                                       |
|----------------|------------------------------------------|-------------------------------------------------------------|
| Format         | Compact, opaque, conda-presto-specific   | in-toto Statement v1 + DSSE + Sigstore bundle (CEP-27 stack)|
| Signing        | HMAC-SHA256 (shared secret)              | Sigstore (keyless, OIDC-bound, ephemeral keys)              |
| Verifiable by  | The same instance, or one with the same secret | Anyone, anywhere, against the public Rekor log           |
| Wire size      | ~200 bytes                               | ~2-5 KB                                                     |
| Latency cost   | µs                                       | ~100-500 ms (Rekor publication when enabled)                |
| Use case       | "Did *this* server produce this lockfile?" | "Did *some recognized identity* produce this lockfile?"   |

Both layers coexist. Most users want the receipt; regulated /
enterprise / cross-org users want attestations. receipt ships first
because it's small and adds immediate value; [attestation](attestation.md) follows.

## Motivation

This plan shifts conda-presto from "fast solver" to "verifiable
solver" — a meaningful capability upgrade.

- The receipt composes with every other plan (transcoder, diff,
  permalink, MCP) without modifying them. Small implementation.
- Solves real, repeated pain (lockfile rot, "it worked yesterday",
  supply-chain trust).
- Reproducibility primitives are an active area in the conda
  ecosystem alongside CEP-27.
- A verifying solver requires the channel-snapshot infrastructure
  that conda-presto naturally provides.

## API surface

### Receipt emission

```
POST /resolve?spec=numpy&format=conda-lock-v1&receipt=true
  → 200 OK
    Content-Type: application/yaml
    X-Solve-Receipt: <opaque base64>          # ~200 bytes
    body: <conda-lock.yml>
```

`?receipt=true` is opt-in for v1 (zero overhead for callers that
don't care). Future: consider making it default once the field
shakes out.

When `?format=` is JSON (default) and `?receipt=true`, the receipt
is included as a top-level `receipt` field in the response body
instead of as a header.

### Receipt contents (illustrative; opaque to clients)

```json
{
  "v": 1,
  "request_hash": "3a7f...",
  "channels": [
    {
      "url": "https://conda.anaconda.org/conda-forge",
      "subdir": "linux-64",
      "repodata_sha256": "8e2f...",
      "fetched_at": "2026-04-16T18:30:00Z"
    }
  ],
  "solver": {"name": "rattler", "version": "0.X.Y"},
  "presto": {"version": "0.X.Y"},
  "solved_at": "2026-04-16T18:30:01Z",
  "sig": "<HMAC-SHA256 over the canonical encoding>"
}
```

The receipt is HMAC-signed with a server-side key
(`CONDA_PRESTO_RECEIPT_SECRET`). Verification requires either the
same instance or one configured with the same secret.

### `/verify` endpoint

```
POST /verify
  Content-Type: application/json
  {
    "lockfile": "<lockfile content>",
    "receipt": "<base64 receipt>"
  }
  → 200 OK
  {
    "verified": true,
    "receipt_age_seconds": 86400,
    "channel_state_drift": false,
    "would_resolve_identically": true,
    "drift": null
  }
```

When channel state has drifted:

```json
{
  "verified": false,
  "receipt_age_seconds": 86400,
  "channel_state_drift": true,
  "would_resolve_identically": false,
  "drift": {
    "current_repodata_sha256": "f12a...",
    "drifted_packages": [
      {
        "name": "openssl",
        "lockfile": {"version": "3.2.0", "build": "h7f8727e_0"},
        "current":  {"version": "3.2.0", "build": "h7f8727e_1"},
        "lockfile_url_404": false
      }
    ],
    "diff_url": "/r/8a3f...e91b"
  }
}
```

When the receipt signature doesn't validate:

```
→ 400 Bad Request
{ "error": "receipt signature invalid" }
```

### CLI parity (optional, follow-up)

```
conda presto solve numpy --receipt > env.lock --receipt-out receipt.txt
conda presto verify env.lock receipt.txt
```

Defer until HTTP surface stabilizes.

## Key integrations

- **[diff](../capability/diff.md):** A receipt on each side reveals whether a
  diff is *drift* (channel changed) versus *intent* (different input).
- **[permalink](../integration/permalink.md):** Permalinks include the receipt —
  same `/r/<hash>` URL becomes self-verifying.
- **[attestation](attestation.md):** Same underlying fields. The
  receipt is the compact local form; the attestation is the public,
  sigstore-signed form. Single data structure, two encodings.

## Implementation outline

### 1. Capture channel state at solve time

In `conda_presto/resolve.py:build_index`, after constructing the
`RattlerIndexHelper`, also capture
`sha256(canonicalized_repodata_bytes)` per `(channel, subdir)`.
Store this on the index cache entry alongside the helper itself:

```python
index_cache[key] = (index, repodata_hashes)
```

Repodata files are already in memory after the fetch; computing the
hash is microseconds.

This step is shared with [attestation](attestation.md) — both receipts and attestations
need channel snapshot hashes.

### 2. New `conda_presto/receipt.py` module

```python
@dataclass(frozen=True)
class Receipt:
    request_hash: str
    channels: list[ChannelSnapshot]      # sorted, canonical
    solver_name: str
    solver_version: str
    presto_version: str
    solved_at: datetime

    def encode(self, secret: bytes) -> str:
        # canonical JSON + HMAC-SHA256 + base64
        ...

    @classmethod
    def decode(cls, encoded: str, secret: bytes) -> Receipt:
        # base64 + HMAC verify + JSON
        ...
```

Pure functions, easily unit-tested. No external state.

The `Receipt` dataclass should be reusable as the in-memory
representation that [attestation](attestation.md)'s attestation builder also consumes
(separate signing/encoding paths, same upstream data).

### 3. Wire emission into `run_solve`

When `?receipt=true` is set, capture the channel hashes at solve
time, build the receipt, and attach to the response (header or
JSON field depending on `?format=`).

### 4. New `POST /verify` handler in `app.py`

```python
@post("/verify", status_code=200)
async def verify(...) -> Response:
    receipt = Receipt.decode(payload.receipt, secret=RECEIPT_SECRET)
    current_hashes = await fetch_current_repodata_hashes(receipt.channels)
    drift = compare_hashes(receipt.channels, current_hashes)
    if not drift:
        return ok_response(receipt, drift=None)
    # Re-solve and produce a structured drift report.
    fresh_envs = await run_solve_for_receipt(receipt)
    return drift_response(receipt, drift, lockfile_vs_fresh_diff)
```

### 5. New config knob

`CONDA_PRESTO_RECEIPT_SECRET` — required for production, generates
an in-memory random secret with a warning if unset (dev convenience).

Document the operational implication: rotating the secret
invalidates all outstanding receipts.

## Tests

- Receipt round-trip: solve → encode → decode → fields match
- HMAC verification: tampering with the receipt body → verify fails
- HMAC verification: wrong secret → verify fails
- `/verify` happy path: lockfile + receipt from the same solve →
  verified=true
- `/verify` channel drift: monkeypatch repodata fetch to return a
  different hash → verified=false, drift report populated
- `/verify` package drift: repodata changed, package was rebuilt →
  drifted_packages list is correct
- `/verify` URL 404: package URL no longer reachable →
  `lockfile_url_404: true` in the drift entry
- Multi-channel receipt: drift in one channel reported correctly
- Receipt format version: v1 only for now; reject unknown `v`
- `?receipt=true` with `?format=` JSON → receipt in body
- `?receipt=true` with `?format=conda-lock-v1` → receipt in header

## Open questions

- **Q1: How do we fetch "current" repodata for `/verify` cheaply?**
  Option A: re-use the in-process index cache (fast, but limited to
  channels we've recently solved against). Option B: do a fresh fetch
  with `If-None-Match` to short-circuit. Probably both: A on cache
  hit, B otherwise.
- **Q2: Should the receipt include the request body itself
  (specs / inline file content)?** No — the `request_hash` is
  enough, and including the body bloats the receipt. The hash means
  `/verify` needs the original lockfile to do meaningful work, which
  is fine: that's the point.
- **Q3: Cross-instance verification.** If instance A signs a receipt
  and the user POSTs it to instance B, B can only verify if it
  shares the same secret. Document: shared secrets enable a
  verifying fleet; defaulting to per-instance secrets is fine for
  solo deployments. Public deployment uses a stable secret. Cross-org
  verification is the proper job of [attestation](attestation.md) attestations, not
  receipts.
- **Q4: Receipt expiry.** Should receipts have an explicit
  `not_after`? Pro: bounds the verification window. Con: legitimate
  reproducibility of old solves becomes harder. For v1 don't set
  expiry; let staleness be reported in `receipt_age_seconds` and let
  callers decide.
- **Q5: Channel snapshot canonicalization.** Different repodata
  sources (CDN node variation, pretty-printing, etc.) can produce
  byte-different but semantically-identical responses. We may need
  to canonicalize repodata before hashing (sort keys, strip
  whitespace, drop volatile metadata fields) to avoid spurious
  drift reports. Verify against real conda-forge first.

## Effort

- v1 (receipt emission + `/verify` + HMAC): ~2 days
- Tests + drift-report polish: ~1 day
- Operational doc (secret management, rotation): ~½ day
- Total: ~3–4 days, single PR

Best landed AFTER [transcoder](../capability/transcoder.md) but BEFORE [github-action](../integration/github-action.md) (GitHub
Action), so the action's CI workflow can default to running
`/verify` on every push. [attestation](attestation.md) (sigstore attestations) follows
on top, reusing the same channel-snapshot capture.

## Out of scope (covered by other plans or filed below)

- **Public-key / sigstore signatures** — see [attestation](attestation.md). Receipts and
  attestations coexist; receipts are the local-fast path,
  attestations are the public-portable path.
- **Conda CEP for the attestation predicate** — see [cep-solve-attestation](cep-solve-attestation.md).
- **Channel snapshot publication.** A separate "snapshot service"
  that periodically publishes immutable, content-addressed channel
  snapshots so receipts/attestations can reference them by name
  rather than by hash. This is genuinely a separate project.
- **Time-travel solves.** "Solve as if the channel state were T."
  Compelling but requires storing historical repodata (expensive).
  Defer indefinitely.
- **Mutation detection across PR base/head.** GitHub action that
  runs `/verify` on the base lockfile and reports drift in the PR
  comment. Easy follow-up once [github-action](../integration/github-action.md) lands.
