# admit: Policy & admission engine — `POST /admit`

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: nothing strictly. Composes powerfully with [receipt](receipt.md), [attestation](attestation.md), [cep-solve-attestation](cep-solve-attestation.md) (CEP draft), and
[serving-attestations](serving-attestations.md) (sidecar serving). Works as a useful endpoint without them
(channel/license/drift checks are valuable on their own); becomes
*radically* useful once attestations are in the picture.
Implementation order: ship after the trust track ([serving-attestations](serving-attestations.md)) so policies can reference attestations as first-class
inputs.

## TL;DR

Conda-presto pivots from a **producer** of trust signals (solve,
receipt, attestation) to also being an **enforcer** of them. Given
a lockfile (optionally with its receipt or attestation bundle) and
a declarative policy, return one of `admit | warn | deny` together
with a structured rationale.

```
POST /admit
  Content-Type: application/json
  {
    "lockfile": "<conda-lock.yml or pixi.lock>",
    "attestation_bundle": "<base64 sigstore bundle>",   // optional
    "receipt": "<base64 receipt>",                       // optional
    "policy": "<TOML or inline JSON>"
  }
  → 200 OK
  {
    "decision": "deny",
    "policy_id": "myorg/strict-1",
    "policy_sha256": "abc...",
    "lockfile_sha256": "def...",
    "violations": [
      {"rule": "channels.allow",
       "severity": "error",
       "message": "package 'somepkg-1.2.3' from channel 'random-channel' not in allow list",
       "fix": null},
      {"rule": "attestations.require_solve_attestation",
       "severity": "error",
       "message": "no solve attestation supplied; policy requires one"}
    ],
    "warnings": [
      {"rule": "drift.reject_when_channel_drifted",
       "severity": "warn",
       "message": "channel 'conda-forge/linux-64' drifted since solve (~3 days ago)"}
    ],
    "summary": {"errors": 2, "warnings": 1, "rules_evaluated": 11},
    "decided_at": "2026-04-16T18:30:01Z"
  }
```

## Motivation

`/admit` gives the trust work a destination. Without it, attestations
are inert; with it, they become enforceable.

- **Closes the loop on the trust track.** Attestations only matter if
  somebody checks them in their pipeline. Today every consumer
  hand-rolls those checks (or skips them). `/admit` collapses that
  into one call.
- **Policies as first-class artifacts.** They version, they diff
  ([diff](../capability/diff.md) over policy is a natural follow-up), they get
  attestations of their own.
- **Composes with existing plans:**
  - [github-action](../integration/github-action.md) GH Action — `conda-presto/admit@v1` becomes a PR check
  - [permalink](../integration/permalink.md) permalinks — policies and decisions live at `/p/<sha>`
    so an `admit` decision is reproducible by reference
  - [meta-mcp](../integration/meta-mcp.md) MCP — agents call `admit` before suggesting an install
  - [receipt](receipt.md) / [attestation](attestation.md) — attestations satisfy `requires_*` clauses
  - [lint](../capability/lint.md) — both produce structured findings; share output
    schema where sensible
  - [why-not](../capability/why-not.md) — for `deny`-because-of-feasibility, point at
    `/why-not` for diagnosis
- **Compliance story.** Compliance teams want to write `policy.toml`
  once and have CI enforce it. Today they cobble together
  `pip-audit` + `safety` + `osv-scanner` + custom scripts. None
  handle conda properly.

## API surface

### `POST /admit`

```
POST /admit
  Content-Type: application/json
  {
    "lockfile":           "<lockfile content>",       // required
    "lockfile_format":    "conda-lock-v1",            // optional, sniffed if omitted
    "policy":             "<TOML or JSON>",           // required (or policy_url)
    "policy_url":         "https://.../policy.toml",  // optional alternative
    "attestation_bundle": "<base64>",                 // optional
    "receipt":            "<base64>"                  // optional
  }

  → 200 OK
    { "decision": "admit" | "warn" | "deny",
      "policy_id": "...",
      "policy_sha256": "...",
      "lockfile_sha256": "...",
      "violations": [...],
      "warnings": [...],
      "summary": {...},
      "decided_at": "..." }
```

`200 OK` regardless of decision — the HTTP layer reports success at
*evaluating* the policy; whether the lockfile passes is in the body.
This matches `/lint` and `/why-not` and avoids conflating HTTP errors
with policy verdicts.

### `POST /admit?explain=true`

Returns the proof tree for the decision: which rules ran, in what
order, what their inputs were, what their outputs were. Composes
with [explain](../capability/explain.md) (`/explain`) — same shape, different domain.

### `GET /admit/policies/templates`

Discovery for built-in policy templates (see below). Each returns
the template TOML and a short description.

### `POST /admit/policies` ([permalink](../integration/permalink.md) integration)

Optional: cache policies by content-address. Returns a
`/admit/policies/<sha256>` URL. Lets `policy_url` point at a
permalinked policy for fully reproducible admit decisions.

### CLI parity

```
conda presto admit env.lock --policy policy.toml
conda presto admit env.lock --policy https://.../policy.toml \
  --attestation env.lock.bundle --explain
```

## Policy schema (TOML, conservative v1)

The schema is **deliberately small** for v1. We resist any urge to
ship a Rego/Cedar-like DSL until we see real-world policies.

```toml
[policy]
id = "myorg/strict-1"
version = "1.0.0"

[channels]
allow = ["https://conda.anaconda.org/conda-forge",
         "https://conda.anaconda.org/bioconda"]
deny  = []                 # exact-match URL deny list (overrides allow)

[packages]
deny = ["log4j-*", "openssl<3.0"]    # MatchSpec patterns
require_pinned = true                 # all specs must be == or build-pinned

[licenses]
allow = []                            # empty = allow all not denied
deny  = ["GPL-3.0", "AGPL-3.0", "LGPL-3.0"]
unknown = "warn"                      # error | warn | ignore
source  = "package_metadata"          # for v1, only this source

[attestations]
require_solve_attestation     = true
require_publish_attestations  = false   # CEP-27 publish atts on every package
trusted_solver_identities = [
  "https://conda-presto.jezdez.dev",
  "https://github.com/myorg/*",
]
trusted_publisher_identities = [
  "https://github.com/conda-forge/*",
]
max_attestation_age_days = 30

[drift]
reject_when_channel_drifted = "warn"   # error | warn | ignore
                                       # uses [receipt](receipt.md) verify pipeline

[vulnerabilities]                      # optional, requires OSV integration
enabled            = false
max_severity       = "high"            # critical | high | medium | low
unpatched_max_days = 14
source             = "osv.dev"

[fail]
on = "error"                           # error | warning | none
                                       # which severity flips admit→deny
```

Fields are conservative on purpose:

- All sections optional except `[policy]`.
- Unknown sections / fields produce a `warn`, not an `error` — keeps
  policies forward-compatible as we add features.
- The `fail.on` knob is the single global "what flips this from
  warn to deny" — explicit, auditable, easy to grep for in PRs.

## Built-in policy templates

Stock templates so users can be productive in 30 seconds:

| Template ID                        | Description                                                                |
|------------------------------------|----------------------------------------------------------------------------|
| `presto/conda-forge-only`          | Allow only conda-forge; deny others; fail on error                         |
| `presto/strict-attested`           | Require solve + publish attestations from trusted identities; ≤30 days old |
| `presto/no-copyleft`               | Deny GPL/AGPL/LGPL; warn on unknown                                        |
| `presto/no-known-cve-high`         | OSV check; deny on high+ severity unpatched > 14 days                      |
| `presto/reproducible-pinned`       | Every spec must be `==`-pinned or build-pinned; warn on `>=`               |
| `presto/orgwide-baseline`          | Combination: conda-forge + attested + no-copyleft + no-cve-high            |

`POST /admit?policy_template=presto/conda-forge-only` is the trivial
"I just want the basic check" call.

Templates are versioned and themselves attested ([attestation](attestation.md)). A user
who wants to know "is `presto/orgwide-baseline` v1.2.0 the same
template I evaluated against last quarter?" gets a yes/no via
sigstore.

## Key integrations

- **[github-action](../integration/github-action.md):** `conda-presto/admit@v1`
  as a PR check. Default policy: `presto/conda-forge-only`.
- **[receipt](receipt.md) / [attestation](attestation.md):**
  Drift checks consume receipts; attestation checks consume sigstore
  bundles.
- **[lint](../capability/lint.md):** Same `Finding` shape; lint runs on
  environment files, admit runs on lockfiles.
- **[serving-attestations](serving-attestations.md):** `/admit` can
  fetch attestations from `<lockfile_url>.sigs` automatically.

## Implementation outline

### 1. New `conda_presto/policy.py`

```python
@dataclass(frozen=True)
class Policy:
    id: str
    version: str
    channels:        ChannelsRule | None
    packages:        PackagesRule | None
    licenses:        LicensesRule | None
    attestations:    AttestationsRule | None
    drift:           DriftRule | None
    vulnerabilities: VulnRule | None
    fail_on:         Literal["error", "warning", "none"]

    @classmethod
    def from_toml(cls, src: str) -> Policy: ...
    def sha256(self) -> str: ...
```

Each rule is a small pure function:
`(lockfile, attestation, receipt, context) -> list[Finding]`.

### 2. Rule registry

```python
_RULES: dict[str, RuleFn] = {}

@register("channels.allow")
def _check_channels_allow(lockfile, policy, ctx) -> list[Finding]: ...
```

Adding a rule = one function. Rules are testable in isolation.

### 3. `POST /admit` Litestar handler

Same body-parsing pattern as `/resolve`. Optional Sigstore
verification (delegates to [attestation](attestation.md)'s `attestation.py`). Optional
drift verify (delegates to [receipt](receipt.md)'s `receipt.py` /verify pipeline).

### 4. Optional OSV integration (defer to v1.1)

Vulnerability checks talk to https://api.osv.dev/v1/querybatch.
Bound by a `vuln_check_timeout_ms` cap; degrades gracefully to
"warn: OSV unreachable, vuln check skipped".

### 5. Decision permalinks

When [permalink](../integration/permalink.md) is enabled, every admit decision is content-addressed
and stored. Verifiers can re-run the same decision and prove
determinism.

### 6. Optional decision attestations

When [attestation](attestation.md) is enabled and `?attest_decision=true` is passed,
the admit decision itself gets a sigstore attestation with a new
predicate type (e.g. `attestations-admit-1`). This is a follow-up;
file as future work in [cep-solve-attestation](cep-solve-attestation.md)'s CEP discussion.

## Tests

- Empty policy → admit with no findings
- Channel allow-list violation → deny
- License deny-list violation → deny when `fail_on = "error"`,
  warn otherwise
- License unknown + `unknown = "warn"` → warning, not error
- Attestation required, none supplied → deny
- Attestation required, supplied but identity not in trusted set → deny
- Attestation required, supplied, valid → admit
- Attestation required, supplied, expired (> max age) → deny
- Drift check: receipt provided, no drift → admit
- Drift check: receipt provided, drift detected, `error` mode → deny
- Drift check: receipt provided, drift detected, `warn` mode → warn
- OSV check: stubbed OSV returns CVE → deny when above threshold
- OSV check: OSV times out → graceful degradation (warn)
- `?explain=true` → proof tree shape correct
- Built-in template `presto/conda-forge-only` → end-to-end works
- Multi-platform lockfile → policy applied per platform; aggregate verdict
- Policy with unknown section → warn, not error (forward compat)
- Policy SHA256 round-trip: same policy text → same hash

## Open questions

- **Q1: TOML or YAML or both?** TOML for v1 (matches conda's
  growing TOML preference, pixi.toml, pyproject.toml). YAML import
  via simple converter if asked for.
- **Q2: Local-only OSV cache vs. live network?** Live for v1.1 with
  a cache; offline mode (file-backed OSV snapshot) as a follow-up
  for air-gapped users.
- **Q3: Policy composition (extends/imports)?** Tempting; resist
  for v1. If two policies need to share rules, copy them. Add
  `[policy] extends = "presto/orgwide-baseline"` only when we see
  real demand.
- **Q4: Policy-as-attestation.** Sign policies themselves with
  sigstore so a verifier can confirm "this is the policy we agreed
  on, signed by the security team." Natural follow-up; feeds back
  into [cep-solve-attestation](cep-solve-attestation.md)'s CEP discussion as a third predicate type.
- **Q5: License source-of-truth.** Conda package metadata's
  `license` field is famously unreliable. v1: use what's there,
  surface `unknown` honestly. Follow-up: optional integration with
  ClearlyDefined or scancode.io.
- **Q6: Per-package overrides / exceptions.** "Allow `pkg-X` even
  though it has GPL." Common need; add an `[exceptions]` section
  in v1.1 with explicit per-package waivers (each requiring a
  reason string, for audit).
- **Q7: Fail-closed vs. fail-open on rule errors.** When a rule
  itself crashes (bug in the evaluator, etc.), do we admit or deny?
  v1: fail-closed (deny). Add `[policy] on_evaluator_error = ...`
  knob if real users want to override.

## Effort

- v1 policy schema + evaluator + 5 core rules
  (channels, packages, licenses, attestations, drift): ~3-4 days
- Built-in templates: ~½ day
- `?explain=true` proof-tree output: ~1 day
- Tests + docs + verification examples: ~2 days
- Total: ~1 week for v1

- v1.1 OSV integration + caching: ~1 week
- v1.2 Policy permalinks + decision attestations: ~½ week

Best landed AFTER [receipt](receipt.md) and [attestation](attestation.md)
(so attestation and drift checks have real signals to consume) and
AFTER or alongside [lint](../capability/lint.md), so both endpoints can share the
`Finding` shape.

## Out of scope (file as follow-ups)

- **Full Rego/Cedar/CEL DSL.** Don't. Start with TOML; only
  consider a real DSL if a community of users hits the wall on
  TOML's expressiveness. Most policies in the wild are simple
  rule sets.
- **Author tooling for policies.** A web UI to author a policy.
  Useful but separate project.
- **Policy marketplace.** Shared community policies under
  `presto/community/...` namespaces. Compelling but governance-
  heavy; defer.
- **Automatic remediation.** "This policy denies X; here's the
  closest alternative environment that admits." Tempting but feels
  like an LLM-feature in disguise; defer until clear demand.
- **Threat-intel feeds beyond OSV.** GitHub Advisory Database,
  Snyk, etc. OSV federates most of these already; revisit only on
  request.

## References

- [receipt](receipt.md) — drift signals
- [attestation](attestation.md) — sigstore signals
- [cep-solve-attestation](cep-solve-attestation.md) (CEP draft) — predicate definitions; admit decisions
  could become their own predicate type later
- [serving-attestations](serving-attestations.md) (serving attestations) — `/admit` auto-fetches `.sigs`
- [github-action](../integration/github-action.md) (GitHub Action) — primary consumer
- [meta-mcp](../integration/meta-mcp.md) (MCP) — agent integration
- [lint](../capability/lint.md) — sister Findings-shaped endpoint
- [why-not](../capability/why-not.md) — link target for feasibility-related denies
