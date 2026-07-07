# Proposals

Design proposals for future conda-presto features and integrations.
Full proposal text lives in GitHub issues so discussion, ownership,
and status changes stay in one place. This page is the roadmap index.

## Status legend

- {bdg-success}`shipped` -- implemented and released
- {bdg-warning}`in progress` -- actively being worked on
- {bdg-secondary}`proposed` -- designed but not yet started

## Streams

Proposals cluster into three thematic streams.

`````{tab-set}

````{tab-item} Capability

Verbs the service exposes: solve, transcode, diff, explain, repair,
and preflight.

| Proposal | Status | Summary |
|---|:---:|---|
| [Lockfile transcoder mode](https://github.com/jezdez/conda-presto/issues/11) | {bdg-secondary}`proposed` | Lockfile-in / lockfile-out fast path plus `?solve=false` guardrail |
| [Preflight validation](https://github.com/jezdez/conda-presto/issues/16) | {bdg-secondary}`proposed` | Fast validation surface, including lint-style findings |
| [Repair suggestions](https://github.com/jezdez/conda-presto/issues/13) | {bdg-secondary}`proposed` | Verified, ranked repair suggestions for infeasible solves |
| [Environment / lockfile diff](https://github.com/jezdez/conda-presto/issues/14) | {bdg-secondary}`proposed` | Diff between two environments |
| [Explain package inclusion](https://github.com/jezdez/conda-presto/issues/15) | {bdg-secondary}`proposed` | Cheap dependency-chain explanations for successful solves |
````

````{tab-item} Integration

Where conda-presto plugs into users' workflows: GitHub Action,
broker-managed local service, full conda solver backend, and durable
permalink cache.

| Proposal | Status | Summary |
|---|:---:|---|
| [GitHub Action for CI workflows](https://github.com/jezdez/conda-presto/issues/17) | {bdg-warning}`in progress` | CI-native solve, preflight, diff, and admit workflows |
| [Broker-managed local service](https://github.com/jezdez/conda-presto/issues/39) | {bdg-secondary}`proposed` | User-scoped local HTTP service that preserves warm conda-presto process state |
| [Full conda solver backend](https://github.com/jezdez/conda-presto/issues/40) | {bdg-secondary}`proposed` | `conda --solver=presto` backed by a public-channel local or explicit remote service |
| [Content-addressed solve cache](https://github.com/jezdez/conda-presto/issues/19) | {bdg-secondary}`proposed` | Durable result URLs such as `/r/<hash>` |
````

````{tab-item} Trust

Supply-chain layer: provenance capture, signed solve provenance,
attestation serving, admission control, and CEP draft.

| Proposal | Status | Summary |
|---|:---:|---|
| [Solve provenance field capture](https://github.com/jezdez/conda-presto/issues/20) | {bdg-secondary}`proposed` | Shared request, artifact, solver, and channel snapshot metadata |
| [Signed solve provenance](https://github.com/jezdez/conda-presto/issues/21) | {bdg-secondary}`proposed` | CEP-27-aligned Sigstore solve attestations |
| [Serving solve attestations](https://github.com/jezdez/conda-presto/issues/22) | {bdg-secondary}`proposed` | Durable `/r/<hash>/attestation` URL and `Link` header |
| [Policy and admission engine](https://github.com/jezdez/conda-presto/issues/23) | {bdg-secondary}`proposed` | Policy and admission engine |
| [CEP draft: solve attestation predicate](https://github.com/jezdez/conda-presto/issues/24) | {bdg-secondary}`proposed` | Draft CEP text for solve attestation predicate |
````

`````

## Dependency graph

```{mermaid}
graph TD
    T[transcoder] --> PF[preflight + lint]
    PF --> RPR[repair suggestions]
    T --> RPR
    T --> B[broker local service]
    T --> P[permalink]
    B --> FS[full conda solver]
    P --> FS
    P --> PV[provenance fields]
    PV --> A[signed provenance]
    A --> CEP[CEP draft]
    A --> S[serving]
    P --> S
    S --> AD[admit]
    AD --> GH[github-action]

    D[diff] -.- T
    E[explain] -.- T
    RPR -.- E
    PF -.- B
    D -.- B
    E -.- B
    RPR -.- B
    B -.- P
    PF -.- FS
    RPR -.- FS
    D --> GH
    PF --> GH

    style D stroke-dasharray: 5 5
    style E stroke-dasharray: 5 5
    style B stroke-dasharray: 5 5
    style FS stroke-dasharray: 5 5
```

Diff and explain can interleave once the transcoder foundation is in
place. Repair stays separate from explain because it can run repeated
solver attempts under an explicit budget. The broker-managed local
service is an optional lifecycle layer for preserving warm process
state across local commands. The full solver backend uses that warm
service plus durable cache keys for public-channel conda transactions.

## Conventions

- One issue per proposal.
- Keep the issue body as the complete design record.
- Status updates happen in this index and on the linked issue.
- Proposal status and stream live as issue labels.
- No marketing in proposals. Each issue must justify itself in its
  "Why this earns its place" section.
