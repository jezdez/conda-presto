# Roadmap

Detailed future design records live in GitHub issues so discussion, ownership,
labels, and status changes stay in one place. This page is the compact roadmap
index.

## Status legend

- {bdg-success}`shipped` -- implemented in the current codebase
- {bdg-warning}`in progress` -- actively being worked on
- {bdg-secondary}`proposed` -- designed but not yet started

## v0.5 foundations

| Issue | Status | Summary |
|---|:---:|---|
| [Lockfile transcoder mode](https://github.com/jezdez/conda-presto/issues/11) | {bdg-success}`shipped` | Lockfile-in / lockfile-out `/transcode` endpoint and CLI fast path |
| [GitHub Action for CI workflows](https://github.com/jezdez/conda-presto/issues/17) | {bdg-success}`shipped` | Composite action for local CLI or hosted API solve workflows |
| [Content-addressed solve cache](https://github.com/jezdez/conda-presto/issues/19) | {bdg-success}`shipped` | HTTP result cache with durable `/r/<hash>` lookup while entries are retained |

## v0.6 review tools

| Issue | Status | Summary |
|---|:---:|---|
| [Preflight validation](https://github.com/jezdez/conda-presto/issues/16) | {bdg-success}`shipped` | Fast local validation and lint-style findings |
| [Environment / lockfile diff](https://github.com/jezdez/conda-presto/issues/14) | {bdg-success}`shipped` | Platform-aware comparisons between resolved environments and covered lockfiles |
| [Explain package inclusion](https://github.com/jezdez/conda-presto/issues/15) | {bdg-success}`shipped` | Bounded dependency-chain explanations for successful single-platform solves |

## Upcoming streams

`````{tab-set}

````{tab-item} Capability

New solver-facing verbs and review surfaces.

| Issue | Status | Summary |
|---|:---:|---|
| [Repair suggestions](https://github.com/jezdez/conda-presto/issues/13) | {bdg-warning}`in progress` | Verified, ranked repair suggestions for infeasible solves |
````

````{tab-item} Integration

Long-lived local service and conda solver integration.

| Issue | Status | Summary |
|---|:---:|---|
| [Broker-managed local service](https://github.com/jezdez/conda-presto/issues/39) | {bdg-secondary}`proposed` | User-scoped local HTTP service that preserves warm process state |
| [Full conda solver backend](https://github.com/jezdez/conda-presto/issues/40) | {bdg-secondary}`proposed` | `conda --solver=presto` backed by a public-channel local or explicit remote service |
````

````{tab-item} Trust

Provenance, attestation serving, admission control, and CEP alignment.

| Issue | Status | Summary |
|---|:---:|---|
| [Solve provenance field capture](https://github.com/jezdez/conda-presto/issues/20) | {bdg-secondary}`proposed` | Shared request, artifact, solver, and channel snapshot metadata |
| [Signed solve provenance](https://github.com/jezdez/conda-presto/issues/21) | {bdg-secondary}`proposed` | CEP-27-aligned Sigstore solve attestations |
| [Serving solve attestations](https://github.com/jezdez/conda-presto/issues/22) | {bdg-secondary}`proposed` | Durable `/r/<hash>/attestation` URL and `Link` header |
| [Policy and admission engine](https://github.com/jezdez/conda-presto/issues/23) | {bdg-secondary}`proposed` | Policy checks over solved artifacts before installation |
| [CEP draft: solve attestation predicate](https://github.com/jezdez/conda-presto/issues/24) | {bdg-secondary}`proposed` | Draft CEP text for a solve attestation predicate |
````

`````

## Dependency graph

```{mermaid}
graph TD
    T["transcode\n(shipped)"] --> PF["preflight + lint\n(shipped)"]
    T --> RPR["repair suggestions"]
    T --> D["diff\n(shipped)"]
    T --> E["explain\n(shipped)"]
    P["result cache + permalink\n(shipped)"] --> PV["provenance fields"]
    P --> B["broker-managed local service"]
    B --> FS["full conda solver backend"]
    P --> FS
    PF --> RPR
    E --> RPR
    PV --> A["signed provenance"]
    A --> CEP["CEP draft"]
    A --> S["attestation serving"]
    P --> S
    S --> AD["policy + admission"]
    GH["CI action\n(shipped)"] --> PF
    GH --> D

    classDef shipped fill:#e6f4ea,stroke:#1e7e34,color:#0b3d1f;
    class T,P,GH,PF,D,E shipped;
```

Diff and explain can use existing lockfile records when the requested platform
is covered. Repair stays separate from explain because it may run repeated
solver attempts under an explicit budget. The broker-managed local service and
full solver backend build on warm process state plus public-channel cache keys.

## Conventions

- One issue per proposal.
- Keep the issue body as the complete design record.
- Status updates happen in this index and on the linked issue.
- Proposal status and stream live as issue labels.
- No marketing in proposals. Each issue must justify itself in its own problem
  statement.
