# Proposals

Design proposals for future conda-presto features and integrations.
Each proposal is a self-contained document with rationale, API surface,
test strategy, open questions, and effort estimate.

## Status legend

- {bdg-success}`shipped` -- implemented and released
- {bdg-warning}`in progress` -- actively being worked on
- {bdg-secondary}`proposed` -- designed but not yet started

## Streams

Proposals cluster into three thematic streams.

`````{tab-set}

````{tab-item} Capability

Verbs the service exposes: solve, transcode, diff, explain, lint,
why-not, preflight.

| Proposal | Status | Summary |
|---|:---:|---|
| {doc}`capability/transcoder` | {bdg-secondary}`proposed` | Lockfile-in / lockfile-out without re-solving |
| {doc}`capability/lint` | {bdg-secondary}`proposed` | Environment-file linter, ~15 rules, sub-50 ms |
| {doc}`capability/why-not` | {bdg-secondary}`proposed` | Solver conflict chains and suggested relaxations |
| {doc}`capability/diff` | {bdg-secondary}`proposed` | Diff between two environments |
| {doc}`capability/explain` | {bdg-secondary}`proposed` | Why is package X in my env? |
| {doc}`capability/preflight` | {bdg-secondary}`proposed` | Sub-100 ms validation without solving |
````

````{tab-item} Integration

Where conda-presto plugs into users' workflows: GitHub Action,
permalink cache, MCP.

| Proposal | Status | Summary |
|---|:---:|---|
| {doc}`integration/github-action` | {bdg-warning}`in progress` | GitHub Action wrapping every endpoint |
| {doc}`integration/meta-mcp` | {bdg-warning}`in progress` | MCP tools via conda-meta-mcp |
| {doc}`integration/permalink` | {bdg-secondary}`proposed` | Content-addressed solve cache |
````

````{tab-item} Trust

Supply-chain layer: receipts, attestations, sidecar serving,
admission control, CEP draft.

| Proposal | Status | Summary |
|---|:---:|---|
| {doc}`trust/receipt` | {bdg-secondary}`proposed` | HMAC receipts + drift detection |
| {doc}`trust/attestation` | {bdg-secondary}`proposed` | Sigstore solve attestations |
| {doc}`trust/serving-attestations` | {bdg-secondary}`proposed` | lockfile.sigs sidecar distribution |
| {doc}`trust/admit` | {bdg-secondary}`proposed` | Policy and admission engine |
| {doc}`trust/cep-solve-attestation` | {bdg-secondary}`proposed` | Draft CEP text for solve attestation predicate |
````

`````

## Dependency graph

```{mermaid}
graph TD
    T[transcoder] --> L[lint]
    T --> W[why-not]
    T --> P[permalink]
    P --> R[receipt]
    R --> A[attestation]
    A --> CEP[CEP draft]
    A --> S[serving]
    S --> AD[admit]
    AD --> GH[github-action]
    AD --> MCP[meta-mcp]

    D[diff] -.- T
    E[explain] -.- T
    PF[preflight] -.- T

    style D stroke-dasharray: 5 5
    style E stroke-dasharray: 5 5
    style PF stroke-dasharray: 5 5
```

Diff, explain, and preflight can interleave anywhere once the
transcoder foundation is in place.

## Conventions

- One file per proposal.
- Cross-references use relative links by slug (no numbers).
- Status updates happen in this index, not by renaming files.
- No marketing in proposals. Each must justify itself in its
  "Why this earns its place" section.

```{toctree}
:hidden:

capability/transcoder
capability/lint
capability/why-not
capability/diff
capability/explain
capability/preflight
integration/github-action
integration/meta-mcp
integration/permalink
trust/receipt
trust/attestation
trust/serving-attestations
trust/admit
trust/cep-solve-attestation
```
