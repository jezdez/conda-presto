# conda-presto plans

Forward-looking plans for `conda-presto`. Each file is a
self-contained markdown plan with rationale, surface, tests, open
questions, and effort estimate.

**Files are numbered by recommended implementation order.** Renumber
freely when priorities shift; the descriptive slug is the durable
identifier.

Status legend: 📋 proposed · 🚧 in progress · ✅ shipped.

| #  | File                                                            | Stream      | Status | One-line                                                                |
|---:|-----------------------------------------------------------------|-------------|:------:|-------------------------------------------------------------------------|
| 01 | [transcoder](01-transcoder.md)                                  | capability  |   📋   | Lockfile-in / lockfile-out without re-solving (`?solve=auto\|always\|never`) |
| 02 | [lint](02-lint.md)                                              | capability  |   📋   | `/lint` — environment-file linter, ~15 rules, sub-50 ms                |
| 03 | [why-not](03-why-not.md)                                        | capability  |   📋   | `/why-not` — solver conflict chains and suggested relaxations          |
| 04 | [permalink](04-permalink.md)                                    | integration |   📋   | Content-addressed solve cache (`/r/<sha>`); anchor for sidecars         |
| 05 | [receipt](05-receipt.md)                                        | trust       |   📋   | Solve receipts (HMAC) + `POST /verify` for local drift detection        |
| 06 | [attestation](06-attestation.md)                                | trust       |   📋   | Sigstore solve attestations (CEP-27 aligned)                            |
| 07 | [serving-attestations](07-serving-attestations.md)              | trust       |   📋   | `<lockfile>.sigs` sidecar (mirrors [conda/ceps#142])                    |
| 08 | [admit](08-admit.md)                                            | trust       |   📋   | `POST /admit` — policy & admission engine                               |
| 09 | [github-action](09-github-action.md)                            | integration |   📋   | `actions/conda-presto@v1` — wraps every endpoint                        |
| 10 | [meta-mcp](10-meta-mcp.md)                                      | integration |   📋   | Expose endpoints as MCP tools via `conda-meta-mcp`                      |
| 11 | [diff](11-diff.md)                                              | capability  |   📋   | `POST /diff` between two environments                                   |
| 12 | [explain](12-explain.md)                                        | capability  |   📋   | `/explain` — why is package X in my env?                                |
| 13 | [preflight](13-preflight.md)                                    | capability  |   📋   | `POST /preflight` — sub-100 ms validation without solving               |
| 14 | [cep-solve-attestation](14-cep-solve-attestation.md)            | trust       |   📋   | Draft CEP text for `attestations-solve-1` predicate (parallel work)     |

[conda/ceps#142]: https://github.com/conda/ceps/pull/142

## Streams

Plans cluster into three thematic streams. The numbered ordering
above interleaves them by dependency and value.

- **capability** — verbs the service exposes (solve, transcode,
  diff, explain, lint, why-not, preflight)
- **integration** — where conda-presto plugs into users' workflows
  (Action, permalink cache, MCP)
- **trust** — supply-chain layer (receipts, attestations, sidecar
  serving, admit, CEP draft)

## Sequencing rationale

- **01 transcoder** comes first: refactors the parser layer to
  return `Environment` objects and exposes `environment_format`.
  Foundation for nearly everything else.
- **02 lint** and **03 why-not** are tiny side quests (~3-4 days
  each), no dependencies, immediate user value. Ship them while
  ramping up to the trust track.
- **04 permalink** anchors both the integration story (caching) and
  the trust story (sidecar URLs).
- **05-08** are the trust track. Build bottom-up: snapshot capture
  (receipt) → sigstore signing (attestation) → distribution
  convention (serving) → enforcement (admit).
- **09 github-action** lands after the trust track so the Action
  can wrap solve, lint, why-not, verify, sidecar fetch, and admit
  in a single composite Action.
- **10 meta-mcp** ships once the underlying endpoints exist. No
  hard ordering with 09.
- **11-13** (diff, explain, preflight) are small and additive;
  interleave wherever they fit.
- **14 CEP draft** runs in *parallel* with 06-08; submit upstream
  once 06 demos cleanly so we have a reference implementation.

## Dependency graph

```
                       01 transcoder
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         02 lint         03 why-not      04 permalink
                                              │
                                              ▼
                                         05 receipt
                                              │
                                              ▼
                                         06 attestation ───── 14 CEP draft
                                              │                  (parallel)
                                              ▼
                                         07 serving
                                              │
                                              ▼
                                         08 admit
                                              │
                              ┌───────────────┴───────────────┐
                              ▼                               ▼
                       09 github-action                 10 meta-mcp

              (11 diff, 12 explain, 13 preflight interleave anywhere)
```

## Conventions

- **One file per plan.**
- **Filename = `NN-slug.md`** where `NN` is current implementation
  order. Slug is the durable identifier; cross-references in prose
  use the slug, not the number.
- **Renumber freely** when priorities shift. The number is metadata,
  not identity.
- **Status updates in this README**, not by renaming files. Move
  the emoji from 📋 to 🚧 to ✅ as plans advance.
- **No marketing in plans.** Each plan must justify itself in its
  "Why this earns its place" section.

## Cross-references

Plan bodies link to each other by slug, e.g.
`[receipt](05-receipt.md)`. If you renumber a plan, update its
filename and any inbound links — the slug stays. Numeric `PLAN-N`
historical IDs are no longer used in prose; if you find one, treat
it as a bug and replace it with a slug link.
