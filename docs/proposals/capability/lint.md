# lint: `/lint` — environment-file linting without solving

Status: implemented (PR #28)
Owner: TBD
Filed: 2026-04-16
Depends on: nothing ([preflight](preflight.md) is the closest neighbor;
this is the smaller, faster, no-network-required cousin).
Implementation order: ship early. No dependencies on the trust track.

## TL;DR

Fast, hosted **style + correctness checker** for conda environment
files. Sub-50 ms. Zero network. Zero solving. Returns a structured
list of findings (warnings, suggestions, errors) with line numbers
and machine-readable codes.

```
POST /lint
  Content-Type: text/x-yaml
  body: <environment.yml | pixi.toml | pyproject.toml | conda.toml>
  → 200 OK
  {
    "findings": [
      {"code": "PIN001", "severity": "warning", "line": 14,
       "message": "spec 'numpy=1.26.4' uses '=' which differs across solvers; prefer '==1.26.4'",
       "fix": "numpy==1.26.4"},
      {"code": "DUP001", "severity": "warning", "line": 22,
       "message": "package 'numpy' is pinned in three places (lines 14, 22, 35)"},
      {"code": "ORD001", "severity": "info", "line": 8,
       "message": "channel order is non-canonical; consider sorting"}
    ]
  }
```

## Motivation

- **Small surface** that adds real value: ~2 days, no new dependencies
  (uses parsers we already ship).
- **No infrastructure cost.** No solving, no network, no cache. Pure
  function over the request body.
- **Editor-friendly.** A LSP-style "lint on save" hook fits this API
  naturally.
- **Compounds with [github-action](../integration/github-action.md) GitHub Action.** "Run conda-presto-lint on
  every PR" is a one-line add. Cheap, fast, helpful comments inline.
- **Independent of the trust track.** No reason to block on the trust track.

## Lint catalogue (initial set, ~15 rules)

Codes are stable identifiers; severity defaults shown but caller-overridable.

| Code   | Severity | Description                                                                       |
|--------|----------|-----------------------------------------------------------------------------------|
| PIN001 | warning  | `name=version` (single `=`) instead of `name==version`                            |
| PIN002 | warning  | Spec uses unqualified package name with no constraint (intent unclear)            |
| PIN003 | info     | Spec pins build string (`name==version=build`) — overconstrained for portability  |
| DUP001 | warning  | Package appears in multiple specs (potential conflict)                            |
| DUP002 | warning  | Package present in both `dependencies` and `pip:` block                           |
| CHN001 | info     | Channel order non-canonical (would change with sort)                              |
| CHN002 | warning  | Channel listed twice                                                              |
| CHN003 | warning  | Deprecated channel alias (e.g. `defaults` when not intended)                      |
| ORD001 | info     | Specs not sorted within group                                                     |
| FMT001 | info     | Mixed indentation (tabs + spaces)                                                 |
| FMT002 | info     | Trailing whitespace                                                               |
| ENV001 | error    | Required field missing (e.g. `name` in `environment.yml`)                         |
| ENV002 | warning  | `prefix:` field set — not portable across machines                                |
| PYP001 | warning  | `pip:` block specifies a package that is also available on conda-forge            |
| PLT001 | info     | Implicit single-platform manifest in a multi-platform-friendly format             |

Catalog is opinionated but small. Each rule is implemented as a pure
function `(parsed_doc) -> list[Finding]`; adding a rule is one file.

## API surface

```
POST /lint
  Content-Type: text/x-yaml | application/toml | text/plain
  Optional headers:
    X-Filename: pixi.toml          # filename hint (same as /resolve)
  Optional query:
    ?ignore=PYP001,FMT002          # disable specific rules
    ?severity=warning              # filter to >= severity
    ?fixable_only=true             # return only findings with a fix
  → 200 OK
    { "findings": [...], "summary": {"errors": 0, "warnings": 3, "info": 1} }
```

CLI parity is trivial:

```
conda presto lint pixi.toml
conda presto lint --ignore PYP001,FMT002 environment.yml
```

## Implementation outline

1. New `conda_presto/lint.py` module with:
   - `Finding` dataclass: `code`, `severity`, `line`, `column`,
     `message`, `fix?`
   - `Linter` class with `register(code, fn)` and `run(parsed_doc)`
   - All rules as small pure functions
2. Reuse the existing parsers (`parse_file_content`) — they already
   return structured docs. Lint runs on the parsed form *and* on the
   raw text (for FMT/whitespace checks).
3. New `POST /lint` Litestar handler — same body parsing as `/resolve`,
   no solving.
4. Litestar OpenAPI: each `code` documented in the response schema's
   `description` so OpenAPI viewers show the catalogue.

## Tests

- One golden-file test per rule (input → expected findings)
- Round-trip: lint → apply suggested fixes → re-lint reports zero
- `?ignore=` filter behavior
- `?severity=` filter behavior
- Multi-format: same logical issue triggers across YAML / TOML / pyproject
- Empty doc → no findings, 200 OK

## Open questions

- **Q1: Auto-fix endpoint?** `POST /lint/fix` returns the rewritten
  doc with safe fixes applied. Tempting but expands scope; defer to
  a follow-up unless GitHub Action wants it (probably will).
- **Q2: LSP server?** A thin LSP wrapper around this API would make
  every editor light up. File as a follow-up project, not part of v1.
- **Q3: Rule severity overrides via config file?** If `pixi.toml` /
  `pyproject.toml` could embed `[tool.conda-presto.lint]` with rule
  overrides, the CLI/API could respect them. Easy add later; v1 takes
  overrides via query string only.
- **Q4: Should `/preflight` ([preflight](preflight.md)) call `/lint` internally?** Probably
  yes — preflight is "lint + spec syntax + channel reachability". A
  shared library function lets both endpoints stay clean.

## Effort

- v1 (10 rules, lint endpoint, tests): ~2 days
- Remaining 5 rules + auto-fix endpoint: ~1 day
- CLI subcommand + docs: ~½ day
- Total: ~3 days, single PR

## References

- [transcoder](transcoder.md), [preflight](preflight.md) — closest neighbors
- [github-action](../integration/github-action.md) (GitHub Action) — primary consumer
- [why-not](why-not.md) (`/why-not`) — sister "fast helper endpoint"
- [admit](../trust/admit.md) (`/admit`) — shares the `Finding` output shape

## Changelog

- 2026-05-08: Implemented v1 with 10 rules (PIN001, PIN002, DUP001,
  CHN001, CHN002, ORD001, FMT001, FMT002, ENV001, ENV002). HTTP
  endpoint at `POST /lint` with `?ignore=` and `?severity=` filters.
  MCP tool metadata included. 40 unit tests + 9 endpoint tests.
  Deferred: PYP001, PLT001, DUP002, PIN003, auto-fix endpoint.
- 2026-04-16: Initial proposal filed.
