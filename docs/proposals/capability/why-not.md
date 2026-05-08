# why-not: `/why-not` — surfacing solver conflict chains

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16
Depends on: nothing strictly. Pairs with [explain](explain.md) and
[preflight](preflight.md). Reuses the same solver index plumbing as
`/resolve`.
Implementation order: ship early. Independent of the trust track.

## TL;DR

When a solve is *infeasible*, the solver knows exactly *why* and
which constraints are in conflict — but conda's UX has historically
buried that information. `/why-not` exposes it as structured JSON.

```
POST /why-not
  Content-Type: application/json
  {
    "specs": ["scipy==1.5", "python==3.13"],
    "channels": ["conda-forge"],
    "platforms": ["linux-64"]
  }
  → 200 OK
  {
    "feasible": false,
    "conflicts": [
      {
        "summary": "no version of scipy==1.5 is available for python==3.13",
        "chain": [
          {"requested": "scipy==1.5"},
          {"resolved_set": ["scipy-1.5.0", "scipy-1.5.1", ..., "scipy-1.5.4"],
           "all_constrain": "python <3.10"},
          {"requested": "python==3.13"},
          {"verdict": "scipy 1.5.x ∩ python 3.13 = ∅"}
        ],
        "minimal_unsatisfiable_subset": ["scipy==1.5", "python==3.13"],
        "suggested_relaxations": [
          {"spec": "scipy==1.5", "relax_to": "scipy>=1.11", "reason": "lowest scipy compatible with python 3.13"},
          {"spec": "python==3.13", "relax_to": "python<3.10", "reason": "highest python compatible with scipy 1.5"}
        ]
      }
    ]
  }
```

## Motivation

- **High pain-to-effort ratio.** "Why can't I install X?" is the #1
  conda support question. The solver already computes this; nobody
  surfaces it.
- **Small.** ~3 days. Pure additive endpoint reusing existing solver
  plumbing.
- **MCP-ready.** Agents asking "can the user have X and Y together?"
  get a structured answer instead of a wall of stderr. Pairs
  naturally with [meta-mcp](../integration/meta-mcp.md).
- **Editor / CI / onboarding sweet spot.** `pixi-browse`, the
  GitHub Action, IDE plugins all want this signal.
- **Independent of the trust track.** Doesn't need the trust track.

## API surface

```
POST /why-not
  Body: same shape as /resolve (specs OR file content)
  Optional query:
    ?suggest_relaxations=true       # default true
    ?max_chain_depth=8              # bound the explanation tree
    ?include_satisfiable_subset=true # bonus: largest subset that DOES solve
  → 200 OK on success regardless of feasibility
    { "feasible": bool,
      "conflicts": [...],
      "satisfiable_subset": [...]?  # if requested
    }
```

A `200 OK` with `feasible: false` is intentional — `/why-not` is a
*query* about feasibility, not a solve attempt. The HTTP layer
shouldn't conflate "the solver disagrees" with "the request was bad".

CLI parity:

```
conda presto why-not scipy==1.5 python==3.13
conda presto why-not --file environment.yml
```

## Composition with existing plans

- **[explain](explain.md).** Same data plumbing (the solver's dependency
  reasoning). `/explain` works on a *successful* solve; `/why-not`
  works on an *unsuccessful* one. Two endpoints, one shared internal
  module.
- **[preflight](preflight.md).** Preflight rejects requests with bad spec
  syntax / unknown packages before even attempting a solve. `/why-not`
  picks up where preflight leaves off — the request is well-formed
  but the solve is infeasible.
- **[diff](diff.md).** When a PR's lockfile would no longer solve
  ("openssl removed from conda-forge"), `/why-not` produces the
  PR-comment text.
- **[meta-mcp](../integration/meta-mcp.md) MCP.** New tool `why_not(specs, ...)` that agents can
  call before suggesting installations.
- **[lint](lint.md).** Lint catches authoring mistakes; why-not catches
  semantic conflicts.
- **[admit](../trust/admit.md).** When admit denies because of feasibility-related
  rules, link to `/why-not` for the conflict chain.

## Implementation outline

### 1. Reuse the solver, capture conflicts

In `conda_presto/resolve.py`, the rattler solver raises a structured
error on infeasibility [needs verification — the conflict graph API
may not be stable across rattler versions]. Catch it, extract the
conflict graph, normalize into our `Conflict` dataclass:

```python
@dataclass(frozen=True)
class Conflict:
    summary: str
    chain: list[ChainStep]
    minimal_unsatisfiable_subset: list[str]
    suggested_relaxations: list[Relaxation]
```

Most of the work is shaping the rattler error into this dataclass.
The solver knows the "minimal unsatisfiable subset" (MUS) algorithm;
we expose it.

### 2. Suggested relaxations

A simple greedy algorithm, runs after the failed solve:

```
for spec in MUS:
    for relaxation in candidate_relaxations(spec):  # widen, drop, etc.
        if solve(MUS - {spec} | {relaxation}).feasible:
            yield Relaxation(spec, relaxation)
            break
```

Bounded budget (re-solve count cap, ~5) so latency stays predictable.
Cache index between attempts (already cached at the request level).

### 3. New `POST /why-not` Litestar handler

Same body-parsing path as `/resolve`. Single new module
`conda_presto/why_not.py` for the conflict normalization and
relaxation search.

## Tests

- Trivially-infeasible pair: scipy==1.5 + python==3.13 → returns
  expected MUS and at least one relaxation
- Feasible request → `feasible: true`, `conflicts: []`
- Spec uses non-existent package → handled (defer to preflight-ish
  error)
- Relaxation budget exceeded → returns conflict but
  `suggested_relaxations: []`
- File-content body (environment.yml with conflict) → works the same
  as inline specs
- Multi-platform: conflict on one platform but not another → reported
  per-platform
- Channel order matters → conflict reported with channel context

## Open questions

- **Q1: How chatty should the chain be?** rattler's internal conflict
  graph can be large. Truncate at `max_chain_depth` (default 8) and
  link to a `?max_chain_depth=999` rerun for full detail.
- **Q2: Relaxation candidate space.** Drop the constraint? Widen the
  version range? Drop the package entirely? For v1: only "drop the
  pin / widen to `>=lowest_compatible`". Add more strategies later
  if real users want them.
- **Q3: Cache the failure?** `/why-not` results are deterministic for
  a given input + channel snapshot. [permalink](../integration/permalink.md) permalinks can cache them
  the same way as solves. Worth it for repeated CI invocations.
- **Q4: Latency budget.** A failed solve + ~5 relaxation re-solves
  could push latency to several seconds for big environments.
  Acceptable because /why-not is interactive ("I just hit an error").
  Document a `max_total_ms` cap; return partial results on timeout.

## Effort

- v1 (conflict normalization + endpoint): ~2 days
- Suggested relaxations + greedy search: ~1 day
- CLI subcommand + docs + tests: ~½ day
- Total: ~3-4 days, single PR

## References

- [explain](explain.md) — sister endpoint for successful solves
- [preflight](preflight.md) — runs before /why-not in CI pipelines
- [meta-mcp](../integration/meta-mcp.md) (MCP) — primary "why-not" consumer
- [lint](lint.md) — catches different class of errors
- [admit](../trust/admit.md) — link target for feasibility-related denies
