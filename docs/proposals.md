# Roadmap

Detailed design records live in GitHub issues so discussion and ownership stay
with the work. This page is the compact release-status index for the current
codebase.

## Status legend

- {bdg-success}`shipped` -- implemented in the current codebase
- {bdg-warning}`in progress` -- actively being worked on
- {bdg-secondary}`proposed` -- designed but not yet started

## v0.5 foundations

| Issue | Status | Summary |
|---|:---:|---|
| [Lockfile transcoder mode](https://github.com/jezdez/conda-presto/issues/11) | {bdg-success}`shipped` | CLI lockfile-in / lockfile-out fast path and metadata-only HTTP validation |
| [GitHub Action for CI workflows](https://github.com/jezdez/conda-presto/issues/17) | {bdg-success}`shipped` | Composite action for local CLI or hosted API solve workflows |
| [Content-addressed solve cache](https://github.com/jezdez/conda-presto/issues/19) | {bdg-success}`shipped` | HTTP result cache with durable `/r/<hash>` lookup while entries are retained |

## v0.6 review tools

| Issue | Status | Summary |
|---|:---:|---|
| [Preflight validation](https://github.com/jezdez/conda-presto/issues/16) | {bdg-success}`shipped` | Deterministic local input checks and lint-style findings |
| [Environment / lockfile diff](https://github.com/jezdez/conda-presto/issues/14) | {bdg-success}`shipped` | Platform-aware comparisons between resolved HTTP inputs |
| [Explain package inclusion](https://github.com/jezdez/conda-presto/issues/15) | {bdg-success}`shipped` | Bounded dependency-chain explanations for successful single-platform solves |

## v0.7 repair and solver service

| Issue | Status | Summary |
|---|:---:|---|
| [Repair suggestions](https://github.com/jezdez/conda-presto/issues/13) | {bdg-success}`shipped` | Tested single-spec relaxations for infeasible solves |
| [Broker-managed local service](https://github.com/jezdez/conda-presto/issues/39) | {bdg-success}`shipped` | Manual loopback HTTP service with a persistent solver worker |
| [Internal conda solver backend](https://github.com/jezdez/conda-presto/issues/40) | {bdg-success}`shipped` | Local final-state delegation through the broker service |
| [Recorded solver requests](https://github.com/jezdez/conda-presto/issues/77) | {bdg-success}`shipped` | Bounded request catalog for scheduled solver-cache refresh |
| [Scheduled solver-cache refresh](https://github.com/jezdez/conda-presto/issues/78) | {bdg-success}`shipped` | Foreground-aware refresh of missing or stale private final states |

## Future work

Provenance, attestation serving, admission control, and CEP alignment.

| Issue | Status | Summary |
|---|:---:|---|
| [Solve provenance field capture](https://github.com/jezdez/conda-presto/issues/20) | {bdg-secondary}`proposed` | Shared request, artifact, solver, and channel snapshot metadata |
| [Signed solve provenance](https://github.com/jezdez/conda-presto/issues/21) | {bdg-secondary}`proposed` | CEP-27-aligned Sigstore solve attestations |
| [Serving solve attestations](https://github.com/jezdez/conda-presto/issues/22) | {bdg-secondary}`proposed` | Durable `/r/<hash>/attestation` URL and `Link` header |
| [Policy and admission engine](https://github.com/jezdez/conda-presto/issues/23) | {bdg-secondary}`proposed` | Policy checks over solved artifacts before installation |
| [CEP draft: solve attestation predicate](https://github.com/jezdez/conda-presto/issues/24) | {bdg-secondary}`proposed` | Draft CEP text for a solve attestation predicate |

## Dependency graph

```{mermaid}
graph TD
    T["transcode\n(shipped)"] --> PF["preflight + lint\n(shipped)"]
    T --> RPR["repair suggestions\n(shipped)"]
    T --> D["diff\n(shipped)"]
    T --> E["explain\n(shipped)"]
    P["result cache + permalink\n(shipped)"] --> PV["provenance fields"]
    P --> B["broker-managed local service\n(shipped)"]
    B --> FS["internal Presto solver backend\n(shipped)"]
    FS --> WC["recorded solver requests\n(shipped)"]
    WC --> CW["scheduled solver-cache refresh\n(shipped)"]
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
    class T,P,GH,PF,D,E,RPR,B,FS,WC,CW shipped;
```

HTTP lockfile uploads remain metadata-only because materializing their package
records can fetch package URLs. Repair stays separate from explain because it
may run repeated solver attempts under an explicit budget. The internal Presto
solver backend
uses the broker-managed local process and does not define a remote protocol.
Its final-state entries share the configured cache storage and
in-memory bounds, but remain separate from public `/resolve` permalinks.
Successful foreground requests can be recorded as candidates for later cache
refresh without changing the final-state cache key. When the catalog is full, a
recorded request enters only after its count, then recency, outranks the
lowest-ranked candidate.

## Conventions

- Use one issue per planned change.
- Keep the issue body as the complete design record while work is active.
- Use this page for shipped status and release grouping.
- Treat closed issues as historical design and discussion records.
- No marketing in proposals. Each issue must justify itself in its own problem
  statement.
