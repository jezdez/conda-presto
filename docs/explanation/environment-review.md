# Environment review model

conda-presto separates input inspection, solving, repair, comparison, and
dependency tracing because they answer different questions. Treating them as
one operation would make a successful response carry more meaning than the
underlying evidence supports.

## Questions and evidence

The table describes the public HTTP operations.

| Operation | Question | Contacts channels | Runs a solver |
|---|---|:---:|:---:|
| `/parse` | What specs and channels does this file declare? | no | no |
| `/preflight` | Is the input locally well formed and does it trigger known review findings? | no | no |
| `/resolve` | Which package records satisfy this request? | normally | normally |
| `/repair` | Does one supported single-spec relaxation make an infeasible request solvable? | yes | yes, repeatedly |
| `/diff` | How do two resolved package states differ? | yes | yes |
| `/explain` | Which dependency chains connect requested specs to one selected package? | yes | yes |
| `/transcode` | Does the request require lockfile package-record materialization? | no | no |

These operations never install packages. A later conda command or another
installer owns any prefix transaction.

## Local findings are not satisfiability

Preflight is deterministic for the supplied content and local configuration.
It can report malformed MatchSpecs, duplicate inputs, pinning patterns, and
other issues without waiting for repodata. That makes it useful before a solve,
but it cannot prove that a package exists or that all constraints are
compatible.

Resolve supplies the missing evidence. Its answer depends on selected channels,
target platforms, virtual packages, solver behavior, and the repodata visible
at solve time.

## Repair is deliberately narrow

Repair starts from an infeasible request and tests a bounded sequence of
single-spec changes. The current strategies remove an exact version or one side
of a simple two-sided version range. They do not change channels, rewrite fuzzy
pins, combine several edits, or choose a preferred solution for the user.

Every returned suggestion has solved on every requested platform. This proves
that the proposed relaxation was feasible against the observed repodata. It
does not prove that the resulting environment matches project policy or user
intent. The caller must review and apply a suggestion explicitly.

The attempt, suggestion, and time limits bound both work and response latency.
`completion_reason` states whether the original request was feasible, every
candidate was exhausted, or a limit ended the search.

## Diff compares states, not intent

Diff resolves each side, then classifies package additions, removals, version
changes, and build changes per platform. The result describes package state. It
does not label a change as safe, compatible, or desirable. An uploaded lockfile
is rejected when the comparison would require its package records.

## Explain follows selected metadata

Explain walks dependency records from requested specs to one selected package.
The traversal is bounded and operates on the solved package state.
`complete: false` records that the local graph could not account for every edge.
It is not a claim that the selected environment is incomplete.

## Why lockfiles can skip work

A lockfile already describes selected package records, but some environment
specifier plugins fetch package URLs when materializing conda's record objects.
The trusted local CLI can use that adapter path for direct lockfile transcoding.
HTTP inspects only format and platform metadata, then rejects operations that
would materialize package records from an untrusted upload.

Use {doc}`../tutorials/review-and-repair` for a guided workflow and
{doc}`../reference/http-api` for exact request and response contracts.
