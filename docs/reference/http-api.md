# HTTP API reference

The conda-presto HTTP API is built on [Litestar](https://litestar.dev/)
and served by uvicorn. Start it with `conda presto --serve` or
`uvicorn conda_presto.app:app --no-access-log`. The conda-presto command is the
preferred entry point because it applies the documented server configuration.

Data endpoints return JSON unless a `format` parameter routes a resolve
response through a conda exporter plugin. Both `/` and
`/openapi.json` serve the generated OpenAPI document as JSON. conda-presto does
not load an interactive API interface or third-party browser assets.

The generated schema covers route discovery and typed request models. It does
not describe the manually dispatched request bodies for `POST /resolve`,
`POST /preflight`, or `POST /transcode`. Their complete body contracts are
documented on this page.

Retained `/resolve` responses include a content-addressed `Location` header
such as `/r/<sha256>`. Repeating the same request against unchanged, acceptable
channel metadata returns the same location while the entry remains available.
An unretained response is still valid but has no `Location` header.

## Public endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/resolve` | Resolve repeated query-parameter specs |
| `POST` | `/resolve` | Resolve a JSON request or raw environment file |
| `POST` | `/preflight` | Report deterministic local input findings |
| `POST` | `/repair` | Test bounded single-spec relaxations |
| `POST` | `/diff` | Compare two selected package states |
| `POST` | `/explain` | Trace dependency chains to one package |
| `POST` | `/transcode` | Convert one lockfile format to another without solving |
| `POST` | `/parse` | Extract specs and channels from a file |
| `GET` | `/r/{hash}` | Fetch one retained resolve response |
| `GET` | `/formats` | List registered exporter names and aliases |
| `GET` | `/platforms` | List conda's known platform subdirectories |
| `GET` | `/version` | Report installed component versions |
| `GET` | `/health` | Report persistent-worker readiness |
| `GET` | `/` | Serve the generated OpenAPI 3.1 document |
| `GET` | `/openapi.json` | Serve the generated OpenAPI 3.1 document |

The broker-only `/solver/v1` protocol is guarded, omitted from OpenAPI, and not
a public integration surface. See {doc}`solver-backend`.

## Endpoints

### `GET /resolve`

Resolve inline specs via query parameters.

Query parameters
: `spec` (repeatable)
  : Package match spec, e.g. `python=3.12`.

  `channel` (repeatable)
  : Channel to search. Falls back to `CONDA_PRESTO_CHANNELS` when omitted.

  `platform` (repeatable)
  : Target platform subdir. Solves for the host platform when omitted.

  `format`
  : Output format name. When set, the response is routed through the
    matching conda exporter plugin instead of returning the default JSON.

```bash
curl 'http://localhost:8000/resolve?spec=python=3.12&spec=numpy&channel=conda-forge&platform=linux-64'
```

---

### `POST /resolve`

Resolve specs via a JSON body, or upload a raw input file with
Content-Type dispatch.

#### JSON body

Send a `ResolveRequest` object:

```json
{
  "specs": ["python=3.12", "numpy"],
  "channels": ["conda-forge"],
  "platforms": ["linux-64", "osx-arm64"],
  "file": null,
  "filename": null
}
```

All fields are optional, but normal requests must provide either
`specs` or `file`. Query parameters (`spec`, `channel`, `platform`,
`format`, `filename`) are accepted alongside the body. Body fields take
precedence when both are present. `format` is a query-only option.

```bash
curl -sS http://localhost:8000/resolve \
  --json '{"specs":["python=3.12","numpy"],"channels":["conda-forge"],"platforms":["linux-64"]}'
```

Retained responses include:

```text
Location: /r/3a7f...e91b
Cache-Control: no-store
```

Successful responses produced through the mutable resolve cache path use
`no-store` so an HTTP intermediary cannot bypass the server's repodata
freshness check. Validation and other error responses do not all carry this
header. The `Location` points to the separately cacheable immutable resource.

#### Raw file upload

Upload an input file directly by setting an appropriate
Content-Type header. No JSON wrapping is needed.

Accepted Content-Types:

- `application/yaml`
- `application/x-yaml`
- `text/yaml`
- `text/x-yaml`
- `application/toml`
- `application/x-toml`
- `text/toml`
- `text/plain`

These media types select raw-body dispatch and a default filename extension.
An installed conda environment-specifier plugin must still recognize the
resulting file. The required plugin set handles environment YAML, requirements
files, conda-lock v1, and rattler-lock v6. TOML formats require an additional
specifier plugin.

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/resolve?platform=linux-64'
```

Use the `filename` query parameter to pick a specific parser when the
Content-Type is ambiguous. For example, `?filename=pixi.lock` forces
the lockfile parser on a generic YAML upload.

HTTP input deliberately rejects files whose first content line is
`@EXPLICIT`. Use explicit files as exporter output, not as `/resolve`,
`/parse`, `/preflight`, or `/transcode` input.

The isolated HTTP parser rejects YAML aliases and inputs with more than 10,000
structural nodes. This limit is applied before conda constructs environment,
package, or match-spec objects.

HTTP lockfile parsing normally stops after format and platform metadata.
`/resolve`, `/diff`, and `/explain` return HTTP 400 when an uploaded lockfile
would require package records. `/transcode` is the exception. Conda-presto's
format-specific compatibility path reconstructs and serializes temporary
records inside the isolated parser process using the URL and metadata already
present in the lockfile. It does not fetch package archives or return those
records to the server process.

---

### `POST /preflight`

Validate specs or an input file without solving or contacting channels.
It accepts the same JSON body and raw-file Content-Type dispatch as
`POST /resolve`, including `spec`, `channel`, `platform`, and `filename`
query parameters.

The response has deterministic local findings. It reports malformed input,
invalid MatchSpecs, duplicate specs or channels, fuzzy or missing pins,
build pins, a non-portable `prefix`, and basic whitespace issues. It does not
try to determine whether the request is satisfiable.

```bash
curl -sS http://localhost:8000/preflight \
  --json '{"specs":["python=3.13","python=3.13"],"channels":["conda-forge"]}'
```

```json
{
  "ok": true,
  "findings": [
    {"code":"PIN001","severity":"warning","message":"fuzzy equality may be unintended; use == for an exact pin","spec":"python=3.13"},
    {"code":"DUP001","severity":"warning","message":"duplicate conda package spec","spec":"python=3.13"}
  ],
  "summary": {"errors":0,"warnings":2,"info":0}
}
```

---

### `POST /repair`

Return single-spec relaxations for an infeasible inline solve. It accepts
`specs`, `channels`, and `platforms`. It does not parse files or change channels.
Every returned suggestion solves on every requested platform.

The initial strategies relax an exact `==` pin or drop one side of a simple
bounded version range. Fuzzy equality such as `python=3.12` is not rewritten.
Specs containing a package URL or filename are left unchanged. The
`max_suggestions`, `max_attempts`, and `time_budget_ms` must be positive. A
value below the corresponding server limit narrows that request. A higher
value is clamped to the server limit. The time budget includes time spent
waiting for solver capacity. Repair is available only through HTTP and has no
matching CLI command.

```bash
curl -sS 'http://localhost:8000/repair?max_suggestions=3&max_attempts=10' \
  --json '{"specs":["scipy==1.5"],"channels":["conda-forge"],"platforms":["linux-64"]}'
```

```json
{
  "feasible": false,
  "diagnosis": {"kind": "solver_conflict", "summary": "..."},
  "suggestions": [
    {
      "changes": [{"from": "scipy==1.5", "to": "scipy", "strategy": "relax_exact_pin"}],
      "solve_attempts": 1,
      "platforms": ["linux-64"]
    }
  ],
  "completion_reason": "exhausted"
}
```

Candidates are evaluated in input-spec order. For a bounded range, dropping
the upper bound is tried before dropping the lower bound. `solve_attempts`
counts candidate solves, excluding the initial solve, through that suggestion.
`platforms` lists where the suggestion solved.

`completion_reason` is one of `feasible`,
`exhausted`, `suggestion_limit`, `attempt_limit`, or `time_limit`. If the
initial diagnostic solve times out, the endpoint returns HTTP 504 without a
repair result. Once infeasibility is established, a candidate timeout returns
HTTP 200 with `completion_reason: "time_limit"` and any suggestions that
already solved.

---

### `POST /diff`

Compare two resolve inputs. Both `from` and `to` use the `ResolveRequest`
shape from `POST /resolve`. An outer `platforms` list applies to both sides.
HTTP lockfile uploads are rejected when comparison would require their package
records.

```bash
curl -sS http://localhost:8000/diff \
  --json '{"from":{"specs":["python=3.12"]},"to":{"specs":["python=3.13"]},"platforms":["linux-64"]}'
```

The response is keyed by platform and separates added, removed, and changed
packages:

```json
{
  "platforms": ["linux-64"],
  "diff": {
    "linux-64": {
      "added": [
        {
          "manager": "conda",
          "name": "new-package",
          "platform": "linux-64",
          "version": "1.0",
          "build": "h123_0",
          "channel": "conda-forge",
          "subdir": "linux-64",
          "url": "https://conda.anaconda.org/conda-forge/linux-64/new-package-1.0-h123_0.conda"
        }
      ],
      "removed": [],
      "changed": [
        {
          "name": "python",
          "manager": "conda",
          "from": {
            "manager": "conda",
            "name": "python",
            "platform": "linux-64",
            "version": "3.12.11",
            "build": "h9e4cc4f_0_cpython",
            "channel": "conda-forge",
            "subdir": "linux-64",
            "url": "https://conda.anaconda.org/conda-forge/linux-64/python-3.12.11-h9e4cc4f_0_cpython.conda"
          },
          "to": {
            "manager": "conda",
            "name": "python",
            "platform": "linux-64",
            "version": "3.13.5",
            "build": "hec9711d_102_cp313",
            "channel": "conda-forge",
            "subdir": "linux-64",
            "url": "https://conda.anaconda.org/conda-forge/linux-64/python-3.13.5-hec9711d_102_cp313.conda"
          },
          "kind": "upgrade"
        }
      ],
      "unchanged_count": 6
    }
  }
}
```

`platforms` lists the compared platforms in response order. `diff` maps each
listed platform to its `added`, `removed`, and `changed` package arrays plus
`unchanged_count`.

Every package in `added` and `removed`, and each `from` and `to` object in
`changed`, has the `DiffPackage` fields `manager`, `name`, `platform`,
`version`, `build`, `channel`, `subdir`, and `url`. A `ChangedPackage` has
`name`, `manager`, `from`, `to`, and `kind`. `kind` is `upgrade`, `downgrade`,
`version-change`, or `build-change`.

Solver failures return HTTP 422. Inputs that resolve to no common platform
return HTTP 400.

---

### `POST /explain`

Show the dependency chains from requested specs to one selected package. Its
JSON body has the `ResolveRequest` fields plus a required `package` field and
must select exactly one platform (the host platform is used when omitted).

```bash
curl -sS http://localhost:8000/explain \
  --json '{"package":"libzlib","specs":["python"],"platforms":["linux-64"]}'
```

```json
{
  "package": "libzlib",
  "version": "1.3.2",
  "platform": "linux-64",
  "chains": [["python", "libzlib"]],
  "complete": true
}
```

`complete` is false when the bounded local graph cannot account for every
edge, such as a virtual package or an unresolvable dependency record. Missing
packages return HTTP 404, and solver failures return HTTP 422.

---

(http-transcode)=
### `POST /transcode`

Convert one lockfile format to another without running the solver. The input
must already be a lockfile, and `format` must name a lockfile exporter such as
`conda-lock-v1` or `pixi-lock-v6`. Package records are reconstructed from the
lockfile metadata without fetching the referenced package archives.

The no-fetch path supports conda-lockfiles' `conda-lock-v1` and
`rattler-lock-v6` exporters and their aliases. Other registered exporters remain
available to normal solve and CLI export paths but are not assumed to accept
metadata-only records.

Cross-format conda-pypi wheel conversion returns HTTP 400. Rattler lock v6
omits the mapped conda package name, so conversion to conda-lock v1 would need
to guess it. Conversion in the other direction would discard the mapped name
that conda-lock v1 does retain.

The temporary compatibility path also returns HTTP 400 when it cannot carry
source data through conda's environment model without changing package
selection or solver constraints. This can apply even when the source and target
formats match. For conda-lock v1, pip, optional, and non-main packages on a
requested platform are rejected. For rattler lock v6, multiple environments and
PyPI package references are rejected. Constraints, features, Python
site-package paths, duplicate dependency names, and dependency selectors are
rejected when the target is conda-lock v1. Duplicate package metadata,
duplicate or dangling references on a requested platform, mismatched URL
identity, package URLs for the wrong platform, and nonempty Rattler fields that
the installed compatibility model cannot represent are also rejected.
Informational metadata that the model does represent but the target lacks may
be normalized by the exporter.

Successful responses include `Cache-Control: no-store` because the serialized
lockfile can contain credential-bearing package URLs.

Query parameters
: `format`
  : Required output format name.

  `platform` (repeatable)
  : Target platform subdir. Defaults to the host platform when omitted.

  `filename`
  : Hint for the parser when uploading a raw file body.

  `spec` (repeatable)
  : Accepted only as a rejection input. Any supplied spec requires solving,
    so the endpoint returns HTTP 400 instead of ignoring it.

  `channel` (repeatable)
  : Accepted only as a rejection input. Any channel override requires
    solving, so the endpoint returns HTTP 400 instead of ignoring it.

#### JSON body

Send a `TranscodeRequest` object:

```json
{
  "file": "...pixi.lock content...",
  "filename": "pixi.lock",
  "platforms": ["linux-64"],
  "specs": [],
  "channels": []
}
```

`specs` and `channels` are optional rejection-only fields. A non-empty value
returns HTTP 400 because applying it would require a solve.

#### Raw lockfile upload

Upload a lockfile directly by setting an appropriate Content-Type header. Use
`filename` when the content type does not identify the lockfile format.

```bash
curl -sS --data-binary @pixi.lock \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/transcode?filename=pixi.lock&platform=linux-64&format=conda-lock-v1'
```

The request fails with HTTP 400 and a `reasons` array if the input is not a
lockfile, the output format is not a lockfile, the requested platforms are
missing from the input lockfile, or the request includes specs or channel
overrides that would require solving.

---

### `GET /r/{hash}`

Fetch a stored content-addressed resolve result. The body and
Content-Type are the exact stored response from the original `/resolve`
request.

```bash
curl -sS http://localhost:8000/r/3a7f...e91b
```

```text
Cache-Control: public, max-age=86400, immutable
```

Missing entries return HTTP 404:

```json
{"error": "result not in cache; re-POST to recompute"}
```

This retrieval does not repeat the repodata freshness check used by a new
`/resolve` request. The address identifies the stored response, so an older
permalink can remain retrievable after current metadata would produce another
digest. It remains available until eviction or removal from the persistent
store. See {doc}`cache` for key fields, retention, and invalidation rules.

---

### `GET /formats`

Returns the list of registered output format names.

```bash
curl http://localhost:8000/formats
```

```json
{
  "formats": [
    "conda-lock",
    "conda-lock-v1",
    "env.yml",
    "environment-json",
    "environment-yaml",
    "explicit",
    "json",
    "pixi",
    "pixi-lock-v6",
    "rattler-lock-v6",
    "reqs",
    "requirements",
    "txt",
    "yaml",
    "yml"
  ]
}
```

---

### `GET /platforms`

Returns the list of known conda platform subdirs.

```bash
curl http://localhost:8000/platforms
```

```json
{
  "platforms": [
    "emscripten-wasm32",
    "freebsd-64",
    "linux-32",
    "linux-64",
    "linux-aarch64",
    "linux-armv6l",
    "linux-armv7l",
    "linux-ppc64",
    "linux-ppc64le",
    "linux-riscv64",
    "linux-s390x",
    "noarch",
    "osx-64",
    "osx-arm64",
    "wasi-wasm32",
    "win-32",
    "win-64",
    "win-arm64",
    "zos-z"
  ]
}
```

---

### `GET /version`

Returns version info for conda-presto and its key dependencies.

```bash
curl http://localhost:8000/version
```

```json
{
  "conda-presto": "0.x.y",
  "conda": "26.x.y",
  "conda-rattler-solver": "0.1.x",
  "conda-lockfiles": "0.x.y"
}
```

---

### `POST /parse`

Parse an input file and extract its specs and channels without
solving. Useful for validation or for building a UI on top of the
solver.

For a lockfile, HTTP parsing does not materialize package records. The response
therefore contains no specs or channels derived from those records.

```bash
curl -sS http://localhost:8000/parse \
  --json '{
    "file": "channels:\n  - conda-forge\ndependencies:\n  - numpy\n",
    "filename": "environment.yml"
  }'
```

```json
{
  "specs": ["numpy"],
  "channels": ["conda-forge"]
}
```

---

### `GET /health`

Readiness probe. Returns HTTP 200 after the configured persistent solver worker
has loaded its indexes, or whenever persistent-worker mode is disabled.

```json
{"status": "ok"}
```

If the persistent worker stops or becomes unavailable, the endpoint returns HTTP
503 until the server or conda-broker replaces it:

```json
{"status": "unavailable"}
```

---

### `GET /`

The generated OpenAPI 3.1 document as JSON. conda-presto intentionally exposes
no interactive renderer because remotely loaded browser code would execute in
the API's origin.

---

### `GET /openapi.json`

The OpenAPI 3.1 schema generated by Litestar. This is the same document served
at `/`. Manually dispatched bodies for
`POST /resolve`, `POST /preflight`, and `POST /transcode` are not represented.

## Resolve response formats

Without `?format=`, both `/resolve` methods return conda-presto's native
`SolveResult` array. Partial failures are represented per platform. With
`?format=<name>`, the response is rendered by the selected conda exporter and
requires every platform solve to succeed. See {doc}`output-formats` for the
canonical schemas, media types, aliases, and failure behavior.

## Error responses

| Status | Meaning |
|---:|---|
| 400 | Invalid input, unknown format, unsupported media type, disallowed channel, request-cap violation, or incompatible operation |
| 404 | Stored result or requested explanation package not found. The guarded private solver route also appears absent outside its broker service. |
| 413 | Request body too large (exceeds `CONDA_PRESTO_MAX_BODY_BYTES`) |
| 422 | A solve required by `/diff` or `/explain` failed, or the private solver returned a conda solver error |
| 429 | Per-client request rate exceeded when rate limiting is enabled |
| 500 | Unexpected solve or exporter failure |
| 503 | Persistent worker unavailable on `/health` or the private solver route |
| 504 | The solve deadline was reached (`CONDA_PRESTO_SOLVE_TIMEOUT_S`) |

Errors returned explicitly by conda-presto handlers are JSON objects with an
`error` field. Framework-level request validation and middleware responses can
use Litestar's own error shape.

Cache-state inspection runs in non-abandoned worker threads because it uses
conda's process-global platform context. If inspection blocks in dependency,
filesystem, or operating-system code, observed request duration can exceed the
configured solve deadline before the handler returns HTTP 504. See
{doc}`cache` for the complete timeout boundary.

```json
{"error": "Unknown format 'bogus'. Available: conda-lock-v1, environment-json, ..."}
```

## See also

- {doc}`cli`
- {doc}`output-formats`
- {doc}`cache`
- {doc}`environment-variables`
