# preflight: `POST /preflight` — validate without solving

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16

## TL;DR

Sub-100ms validation endpoint. Checks spec syntax, package name
existence, channel reachability, and version-specifier
parsability — without running the SAT solver. Returns line-by-line
errors with "did you mean" suggestions.

## Motivation

- **IDE / editor integration is the killer use case.** Red-squiggle
  a typo in `environment.yml` in real time. Today: no fast feedback
  loop for "did I write this spec correctly?" — you find out by
  waiting 30s for a solve to fail.
- **Cheaply built on what we already have.** The cached
  `RattlerIndexHelper` knows every package name in the channel.
  `MatchSpec` parses every line. The whole endpoint is index lookups
  + `MatchSpec()` calls. No SAT.
- **Pairs with future LSP work** if anyone wants to write a
  language server for `environment.yml` / `pixi.toml`. We become
  the validation backend.

## API surface

```
POST /preflight
  Content-Type: same dispatch as /resolve
  body: same shapes as /resolve
  →
  {
    "ok": false,
    "channels_reachable": true,
    "errors": [
      {
        "spec": "numpyy",
        "line": 3,                            // when input is a file
        "kind": "unknown_package",
        "message": "no package named 'numpyy' in conda-forge",
        "did_you_mean": ["numpy", "numpy-base", "numpyro"]
      },
      {
        "spec": "scipy>=blah",
        "line": 7,
        "kind": "invalid_spec",
        "message": "invalid version specifier: '>=blah'"
      },
      {
        "channel": "https://my-private.example.com/conda",
        "kind": "channel_unreachable",
        "message": "HTTP 403 from channel"
      }
    ],
    "warnings": [
      {
        "spec": "python<3.10",
        "kind": "deprecated",
        "message": "python<3.10 is past EOL"
      }
    ]
  }
```

`ok` is `true` iff `errors` is empty. Warnings don't affect `ok`.

## Implementation outline

1. New module `conda_presto/preflight.py`:
   - `validate_spec(s: str) -> Result` — wraps `MatchSpec(s)` to
     catch parse errors with line context.
   - `validate_package_in_index(name, index) -> Result` — index
     lookup + closest-name suggestions via
     `difflib.get_close_matches`.
   - `validate_channel(url) -> Result` — HEAD request to
     `<channel>/<subdir>/repodata.json`, with timeout.
2. New `POST /preflight` handler in `app.py`. Reuses
   `parse_file_content` for inputs (so it accepts the exact same
   request shapes as `/resolve`).
3. Run validations concurrently per spec via `anyio.create_task_group`.
4. Cache channel reachability for ~60s to avoid hammering on
   repeated preflights from a single client.

## Tests

- Typo'd package name → error with sensible suggestions
- Valid spec with unusual operators (`!=`, `~=`) → no error
- Invalid syntax → error with the offending substring
- Unreachable channel → channel error, but spec validation still
  runs against reachable channels
- File input with line numbers → errors carry correct `line` field
- Warning for EOL Python (smoke test for a soft-fail rule)

## Effort

~1 day. Most of the work is producing useful "did you mean"
suggestions and getting the line-number tracking right for file
inputs.

## Out of scope

- Solver-level conflict detection ("`numpy<2` and `scipy>=1.13`
  are mutually unsatisfiable") — that's what the solve is for.
  Preflight is purely about *individual* spec / channel validity.
- Performance budget enforcement (a separate `/diff` "this would
  pull in 200 packages" check could live here later).
- Persistent rule library for warnings (EOL versions, known
  problematic combos). Start with a hard-coded list of 3-5 rules
  and grow.
