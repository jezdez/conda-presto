# transcoder: Lockfile transcoder mode

Status: proposal, not yet implemented
Owner: TBD
Filed: 2026-04-16

See `../README.md` for the full plan index, the three streams
(capability / integration / trust), and the recommended
implementation order.

## TL;DR

Stop solving when the input is already fully resolved. Add a
`?solve=auto|always|never` knob to `/resolve` (default `auto`). When
both the input and output formats are conda
`EnvironmentFormat.lockfile` and the requested platforms are a subset
of the input's platforms, skip the solver and pipe the parsed
`Environment` straight to the exporter.

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

### New query parameter: `?solve={auto,always,never}`

- `auto` (default): skip the solve iff
  - input specifier's `environment_format == EnvironmentFormat.lockfile`, AND
  - output exporter's `environment_format == EnvironmentFormat.lockfile`, AND
  - the requested platforms are a subset of the platforms the parsed
    `Environment` already covers (or no platforms requested at all).
  Otherwise solve.
- `always`: today's behaviour. Always solve, regardless of input format.
- `never`: error (HTTP 400) if a solve would be needed under `auto`.

### Behaviour matrix (input × output, with `solve=auto`)

|                            | output: env (yaml/json)         | output: lockfile (pixi.lock, conda-lock.yml, explicit) |
|----------------------------|----------------------------------|--------------------------------------------------------|
| input: env (environment.yml, pyproject.toml, requirements.txt, inline specs) | solve, then exporter            | solve, then exporter                                  |
| input: lockfile (pixi.lock, conda-lock.yml, explicit)                         | solve (lockfile → env loses pinning unless we read explicit_packages — see open question Q2) | **transcode, no solve**                             |

The bottom-right cell is the new fast path. Everything else is
unchanged.

### Default output (no `?format=`)

Unchanged. The default JSON `list[SolveResult]` always solves
(implementation-wise it just doesn't go through the exporter path
at all). Could be revisited later — see "Out of scope" below.

## Implementation outline

Files touched: `conda_presto/app.py`, `conda_presto/resolve.py` (small
helpers), `conda_presto/exporter.py` (read `environment_format`),
`tests/test_app.py`. No changes to `cli.py`, `config.py`, or any
public CLI behaviour in this PR.

### 1. Refactor `parse_file_content` to return the parsed `Environment`

Today (`app.py:139`):

```python
def parse_file_content(content, filename=None) -> tuple[list[str], list[str]]:
    ...
    specs = [str(s) for s in env.requested_packages]
    channels = [...]
    return specs, channels
```

Change to:

```python
@dataclass
class ParsedInput:
    env: Environment
    specs: list[str]
    channels: list[str]
    specifier: CondaEnvironmentSpecifier  # for environment_format

def parse_file_content(content, filename=None) -> ParsedInput:
    ...
```

The existing call site in `resolve_post` keeps using `.specs` /
`.channels`; the new transcoder path uses `.env` and `.specifier`.

Tempfile lifetime: keep the same lazy-access pattern (some env-spec
plugins read the file lazily on attribute access). Either materialize
`explicit_packages` eagerly into a list before exiting the
`NamedTemporaryFile` context, or restructure to keep the tempfile
alive for the request lifetime.

### 2. Expose `environment_format` from `exporter.py`

Add a small accessor that returns the exporter's `environment_format`
without needing the calling code to import `CondaEnvironmentExporter`
or the format enum directly:

```python
def output_is_lockfile(format_name: str) -> bool:
    exporter = context.plugin_manager.get_environment_exporter_by_format(format_name)
    return exporter.environment_format == EnvironmentFormat.lockfile
```

(Or just expose the exporter and let `app.py` import the enum.
Either works. Whichever keeps `app.py` cleaner.)

### 3. Branch in `app.py` before `run_solve`

In `resolve_post` (and symmetrically in `resolve_get`, where it just
means "solve, since there's no input lockfile to transcode"):

```python
solve_mode = solve or "auto"          # new query param
parsed: ParsedInput | None = None
if file_content is not None:
    parsed = parse_file_content(file_content, file_name)
    specs = list(specs) + parsed.specs
    if not channels:
        channels = parsed.channels

# ... cap checks, default channels ...

if (
    parsed is not None
    and format_name is not None
    and solve_mode != "always"
    and parsed.specifier.environment_format == EnvironmentFormat.lockfile
    and output_is_lockfile(format_name)
    and platforms_are_subset(platforms, parsed.env)
):
    # Fast path: no solve, just re-emit.
    body, media_type = render_envs([parsed.env], format_name)
    return Response(body, media_type=media_type)

if solve_mode == "never":
    return Response(
        {"error": "solve=never but a solve is required for this combination"},
        status_code=HTTP_400_BAD_REQUEST,
    )

return await run_solve(request, specs, channels, platforms or None, format_name=format_name)
```

`platforms_are_subset(requested, env)` is the only new helper:

```python
def platforms_are_subset(requested: list[str], env: Environment) -> bool:
    if not requested:
        return True
    return set(requested) <= {env.platform, *(getattr(env, "platforms", []) or ())}
```

(Need to verify how the conda-lockfiles loader populates platform
metadata on `Environment` — this is one of the few open questions
below.)

### 4. Tests

Four happy-path cells of the matrix:

1. lockfile in (pixi.lock), lockfile out (conda-lock.yml), `solve=auto` → no solver call
2. lockfile in, env out (environment.yml), `solve=auto` → solver called
3. env in (environment.yml), lockfile out, `solve=auto` → solver called
4. env in, env out, `solve=auto` → solver called

Plus the `solve=` knob:

5. lockfile in, lockfile out, `solve=always` → solver called
6. env in, lockfile out, `solve=never` → 400
7. lockfile in, lockfile out, `solve=never` → success, no solver call
8. lockfile in (linux-64 only), lockfile out, `?platform=osx-64` → solver called (subset check fails)

And one robustness case:

9. lockfile in, lockfile out, transcoder path, malformed input → 400 with helpful message (existing parse error path)

Mock the solver call by monkeypatching `conda_presto.resolve.solve_environments`
to assert it's NOT called on cells 1, 7, and IS called on the rest.

### 5. OpenAPI / docs

- Document `?solve=` in the `POST /resolve` and `GET /resolve` handler
  docstrings (drives the OpenAPI description).
- Update the module docstring at `app.py:1` to describe the
  transcoder mode in two sentences.
- Update README pitch + add a short "Lockfile transcoding" example
  block under the existing examples.
- Add a CHANGELOG entry under `[Unreleased]`.

## API surface change summary

- New query param on `POST /resolve` and `GET /resolve`:
  `?solve=auto|always|never` (default `auto`).
- New behaviour: when `?format=` resolves to a lockfile exporter and
  the input is a lockfile, no solve runs.
- No breaking changes. Existing requests behave identically because
  `auto` only short-circuits when both ends are lockfiles — and today
  no existing client could have been doing that and getting solver
  results, since there was no fast path.

## Migration / backwards compatibility

- Pure addition. Default behaviour is preserved for every input/output
  combination that exists today, *except* lockfile→lockfile, which
  changes from "re-solve and re-emit (slow)" to "re-emit directly
  (fast)". Output content for that cell should be byte-identical for
  well-formed lockfiles, since we're skipping a solve that was
  finding the same packages anyway. Callers that depend on the solve
  side-effects (cache warmup, etc.) can pass `?solve=always`.

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

- **Q1:** Does the `Environment` returned by
  `CondaLockV1Loader` / `RattlerLockV6Loader` populate
  `explicit_packages` in a form the matching exporter's
  `multiplatform_export` accepts? Quick verification: parse a
  `pixi.lock`, render it back via `rattler-lock-v6`, diff against
  the input (expect a stable normalization, not byte-identity).
- **Q2:** What's the right behaviour for lockfile → environment.yml
  output? Three options: (a) always re-solve; (b) emit the parsed
  `Environment` as-is (preserves pinning but loses the "human
  re-solvable" property of environment.yml); (c) emit just the
  `requested_packages` to keep environment.yml semantically
  user-editable. Default to (a) for now (current behaviour); revisit
  if anyone asks.
- **Q3:** Multi-platform lockfile inputs — does the conda-lockfiles
  loader give us one `Environment` per platform or one
  `Environment` with multiple platforms? The exporter signature
  takes `Iterable[Environment]`, so the right structure depends on
  the loader. Affects the `platforms_are_subset` helper.
- **Q4:** How does `?platform=` interact with a lockfile input? If a
  user passes `?platform=osx-64` and the input lockfile only covers
  `linux-64`, today we re-solve. Under transcoder mode, the subset
  check fails and we fall through to a solve. Is that surprising?
  Should we document it as "to filter a lockfile to a subset of its
  platforms, use `?solve=never` plus an explicit platform list"?
- **Q5:** Should `?solve=auto` also short-circuit when the input is a
  lockfile and the output is `default JSON` (no `?format=`)? Pro:
  consistent fast path. Con: the JSON shape (`list[SolveResult]`) is
  technically a solve result, not a lockfile re-emit. Probably no for
  this PR.

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
