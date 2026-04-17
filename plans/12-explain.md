# explain: `/explain` — why is this package in my env?

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: nothing strictly; richer when paired with [diff](11-diff.md)

## TL;DR

Surface the dep-chain rationale that the solver already knows:
"`libstdcxx-ng` is in your env because `numpy` requires `openblas`,
which requires `libstdcxx-ng`." Two surfaces:

- `?explain=true` on `/resolve` adds a `requested_by` chain to each
  package in the response.
- `POST /explain` returns the full chain for a single named package
  given a resolve request.

## Motivation

- The solver already computes this internally. We just throw it away
  when we extract `PackageRecord`s. Reaching into
  `SolverOutputState` to retain the request graph is a small
  read-only addition.
- Real, frequent pain: "why is this thing in my env?" Today's answer
  is "diff two solves" or "use mamba's `--why` flag and squint."
- Pairs naturally with [diff](11-diff.md): "this dep changed because *that*
  package now requires it" becomes a one-call explanation.
- Useful for AI agents ([meta-mcp](10-meta-mcp.md)) that need to reason about why a solve
  produced a particular result.

## API surface

```
GET /resolve?spec=numpy&explain=true
  → adds `requested_by: ["numpy"]` (top-level) or
    `requested_by: ["numpy", "openblas"]` (transitive) per package

POST /explain
  Content-Type: application/json
  {
    ...ResolveRequest...,
    "package": "libstdcxx-ng",
    "platform": "linux-64"          // optional, default first
  }
  →
  {
    "package": "libstdcxx-ng",
    "version": "13.2.0",
    "platform": "linux-64",
    "chains": [
      ["numpy", "openblas", "libstdcxx-ng"],
      ["scipy", "openblas", "libstdcxx-ng"]
    ],
    "alternatives": [               // optional, separate flag
      {"version": "12.3.0", "available": true},
      {"version": "13.1.0", "available": true}
    ]
  }
```

## Implementation outline

1. In `run_solver` (`conda_presto/resolve.py:224`), after solving,
   walk `SolverOutputState` to build a `dict[name, list[name]]`
   mapping each resolved package to its direct requesters within
   the resolved set.
2. New helper `chains_to(name, graph, roots)` does BFS to surface
   all paths from a root user-spec to the target package. Cap depth
   to avoid pathological cycles.
3. Extend `ResolvedPackage` with an optional `requested_by:
   tuple[str, ...]` field (only populated when `?explain=true`).
4. New `POST /explain` handler in `app.py` that runs the solve once
   and returns the chain for the named package. Reject with 404 if
   the package isn't in the solution.

## Tests

- Top-level package → `requested_by` is `[name]`
- Transitive package → at least one valid chain ending at a user spec
- Package not in solution → 404
- Multi-platform → `?platform=` selects the right graph
- Cycle in dep graph (rare but possible) → bounded chain, no hang

## Effort

~2 days. Most of the work is careful retention of the
`SolverOutputState` graph structure (the conda-rattler-solver
internals are the trickiest part — we're already reaching in once
for performance, this would be the second touchpoint).

## Caveats

- Dep graphs from a SAT solver aren't always intuitive — there can
  be multiple equally valid chains to the same package. We expose
  *all* chains rather than picking one.
- The `alternatives` field would re-query the index, which is fast
  but not free. Hide behind a separate `?alternatives=true` flag.

## Out of scope

- Why a *specific version* was chosen (would need to capture
  pinning constraints from the SAT solver).
- Why a package is *missing* (would need to introspect solver
  failure paths — much harder, separate plan if anyone asks).
