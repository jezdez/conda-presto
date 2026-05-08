# transcoder: `POST /transcode` — lockfile format conversion

Status: implemented (PR #27)
Owner: TBD
Filed: 2026-04-16

See `../README.md` for the full plan index, the three streams
(capability / integration / trust), and the recommended
implementation order.

## TL;DR

A dedicated `POST /transcode` endpoint converts between lockfile
formats (pixi.lock, conda-lock.yml, explicit) without invoking the
solver. The parsed `Environment` is piped straight to the exporter.

This turns a ~250 ms re-solve into a ~5 ms transcode, and reframes
conda-presto from "fast solver as a service" to "conda's universal
environment translator."

## Motivation

We just spent a session walking conda's plugin internals
(`docs/source/dev-guide/plugins/environment_specifiers.rst`,
`docs/source/dev-guide/plugins/environment_exporters.rst`,
`conda/plugins/manager.py`, `conda_lockfiles/plugin.py`). Three facts
matter:

1. Conda already exposes a typed `EnvironmentFormat` enum
   (`lockfile` vs `environment`) on every specifier and exporter.
2. `conda-lockfiles` registers the same name (e.g. `conda-lock-v1`,
   `rattler-lock-v6`) in *both* the env-spec hook and the exporter
   hook. Same for the built-in `explicit` format.
3. We already use both registries at runtime
   (`detect_environment_specifier` for input,
   `get_environment_exporter_by_format` for output).

The plugin matrix is a half-built bridge that nobody in the conda
ecosystem has connected end-to-end. Connecting it is the natural
next step:

- No tool today does universal conda lockfile transcoding
  (pixi.lock ↔ conda-lock.yml ↔ explicit ↔ environment.yml).
- Zero new dependencies; moderate change in `app.py` plus a small
  refactor in the parsing helper.
- Solves real, repeated pain — pixi↔conda-lock migrations,
  mixed-tooling teams, "make my pixi.lock consumable by `conda env
  create`" cases.
- The format matrix expands for free as third-party env-spec /
  exporter plugins are installed.

The transcoder reframes the single-endpoint `POST /resolve` design:
the body is "the source environment in whatever format", `?format=`
is "the destination format", and the plugin registries enumerate the
allowed cells of the matrix.

## API surface

### `POST /transcode` — dedicated endpoint

```
POST /transcode?format=pixi-lock-v6
Content-Type: application/json
{"file": "<lockfile content>", "filename": "conda-lock.yml"}
→ 200 OK
Content-Type: application/yaml
body: <pixi.lock>
```

Also accepts raw body with `Content-Type: application/yaml` (same
body parsing as `/resolve`).

Requires `?format=` (400 without it). Both input and output must be
lockfile formats; returns 400 with a clear message otherwise.

### Design decision: separate endpoint vs query param

The original proposal used `?solve=auto|always|never` on `/resolve`.
After review (with input from Opus 4.7 and GPT 5.5), we chose a
dedicated endpoint instead:

- `/resolve` means "solver ran"; `/transcode` means "format
  conversion, no solver." Clear provenance for a supply-chain tool.
- Maps 1:1 to the GitHub Action's `command: transcode`.
- Follows the `/parse` precedent (file operation, no solve).
- Avoids the semantic awkwardness of `/resolve?solve=never`.
- Each endpoint is a distinct MCP tool with its own description.

### Behaviour matrix (endpoint × direction)

| Direction | Endpoint | Solver invoked? |
|-----------|----------|-----------------|
| env → env | `/resolve` | yes |
| env → lockfile | `/resolve` | yes |
| lockfile → lockfile | `/transcode` | no |
| lockfile → env | `/resolve` | yes (re-solve needed) |

### `/resolve` is unchanged

No new query params, no implicit fast paths. `/resolve` always
solves. Callers that want the fast lockfile conversion path use
`/transcode` explicitly.

## Implementation outline

Files touched: `conda_presto/app.py`, `conda_presto/exporter.py`,
`tests/test_app.py`, `tests/test_exporter.py`.

### 1. Refactor `parse_file_content` to return `ParsedInput`

```python
@dataclass
class ParsedInput:
    env: object       # conda.models.environment.Environment
    specifier: object  # conda.plugins.types.CondaEnvironmentSpecifier
    specs: list[str]
    channels: list[str]
```

Carries the parsed `Environment` and specifier plugin alongside
specs/channels. The `/resolve` call sites use `.specs` / `.channels`;
`/transcode` uses `.env` and `.specifier`.

Tempfile lifetime: `explicit_packages` is eagerly materialized into
a list before exiting the `NamedTemporaryFile` context.

### 2. Format-detection helpers in `exporter.py`

```python
def output_is_lockfile(format_name: str) -> bool: ...
def input_is_lockfile(specifier: object) -> bool: ...
```

Both check `environment_format == EnvironmentFormat.lockfile`. The
`EnvironmentFormat` enum is imported lazily to avoid import cost at
startup.

### 3. `POST /transcode` handler in `app.py`

Accepts the same body formats as `/resolve` (JSON envelope or raw
file body). Validates both sides are lockfiles via
`validate_transcode()`. Passes the parsed `Environment` directly
to `render_envs()`.

### 4. Tests

- Missing `?format=` returns 400
- No file content returns 400
- Environment input (not lockfile) returns 400
- Non-lockfile output format returns 400
- Lockfile in + lockfile out succeeds, solver never called
- Raw body dispatch works
- Unknown format returns 400 with available formats
- Unsupported content type returns 400
- MCP tool metadata is set

## API surface change summary

- New `POST /transcode` endpoint with `?format=` (required).
- `/resolve` is unchanged. No new query params, no implicit behaviour
  changes.
- No breaking changes. The new endpoint is purely additive.

## Migration / backwards compatibility

- Pure addition. Existing `/resolve` callers are unaffected.
  Lockfile-to-lockfile conversion that previously went through
  `/resolve` (re-solving) still works, just slower. The new
  `/transcode` endpoint is the fast path for that use case.

## Out of scope (file as follow-ups)

- `POST /diff`: take two lockfiles (or two environment specs),
  return the resolved package diff. Composes naturally on top of the
  transcoder mode (transcode both sides to a canonical form and
  diff).
- `POST /upgrade`: take a lockfile and a list of bumped specs, return
  the new lockfile. Needs partial-resolve support in the solver
  layer.
- Streaming progress for slow solves (WebSocket or SSE).
- `GET /formats` self-describing endpoint that surfaces the env-spec
  and exporter registries (their `name`, `aliases`,
  `default_filenames`, `description`, `environment_format`). Useful
  but separable; this PR can ship without it.
- Default-output transcoder: skip the solve even when the request
  asks for the native JSON `list[SolveResult]` output if the input
  is already a lockfile. Possible but slightly weird semantically
  (we'd be inferring user intent); defer until someone asks.
- `?solve=` on the CLI (`conda presto`). Mirror the behaviour for
  parity once HTTP is stable.

## Open questions

- **Q1: Round-trip fidelity.** Does the `Environment` returned by
  `CondaLockV1Loader` / `RattlerLockV6Loader` populate
  `explicit_packages` in a form the matching exporter's
  `multiplatform_export` accepts? Quick verification: parse a
  `pixi.lock`, render it back via `rattler-lock-v6`, diff against
  the input (expect a stable normalization, not byte-identity).
- **Q2: Multi-platform lockfiles.** Does the conda-lockfiles loader
  give us one `Environment` per platform or one `Environment` with
  multiple platforms? The exporter takes `Iterable[Environment]`, so
  the right structure depends on the loader.
- **Q3: Platform filtering.** Currently `/transcode` does not accept
  `?platform=` to filter a multi-platform lockfile to a subset. The
  exporter receives the full environment. Adding platform filtering
  is a follow-up if needed.

## Effort estimate

- Implementation + tests: ~½ day
- README + CHANGELOG + docstring polish: ~1 hour
- Verification against real `pixi.lock` and `conda-lock.yml`
  fixtures: ~1 hour
- Total: ~1 working day, single PR.

## References

- conda env-spec plugin docs: `conda/docs/source/dev-guide/plugins/environment_specifiers.rst`
- conda exporter plugin docs: `conda/docs/source/dev-guide/plugins/environment_exporters.rst`
- conda plugin types: `conda/conda/plugins/types.py` — `EnvironmentFormat`,
  `CondaEnvironmentSpecifier`, `CondaEnvironmentExporter` (line numbers
  approximate; drift with upstream commits)
- conda plugin manager dispatch:
  `conda/conda/plugins/manager.py` — `detect_environment_specifier`,
  `get_environment_exporter_by_format` (line numbers approximate)
- conda-lockfiles plugin registration: `conda_lockfiles/plugin.py`
  (in the conda-lockfiles package)
- Current parser entry point in conda-presto: `conda_presto/app.py:139` (`parse_file_content`)
- Current exporter entry point: `conda_presto/exporter.py:68` (`render_envs`)
- Current solve dispatch: `conda_presto/resolve.py:393` (`solve_environments`)

## Changelog

- 2026-05-08: Redesigned from `?solve=auto|always|never` query param
  on `/resolve` to a dedicated `POST /transcode` endpoint. Rationale:
  clearer semantics for a supply-chain tool (each endpoint does one
  thing), maps 1:1 to the GitHub Action's `command: transcode`,
  follows the `/parse` precedent, avoids the awkward
  `/resolve?solve=never` semantics. Design reviewed by Opus 4.7 and
  GPT 5.5, both recommended Option B (separate endpoint).
- 2026-04-16: Initial proposal filed with `?solve=` query param design.
