# HTTP API reference

The conda-presto HTTP API is built on [Litestar](https://litestar.dev/)
and served by uvicorn. Start it with `conda presto --serve` or
`uvicorn conda_presto.app:app`.

All endpoints return JSON unless a `format` parameter redirects the
response through a conda exporter plugin.

Successful `/resolve` responses include a content-addressed `Location`
header such as `/r/<sha256>`. Repeating the same request against the
same channel metadata returns the same location.

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

  `filename`
  : Hint for the parser when uploading a raw file via POST. Ignored on GET.

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
  "filename": null
}
```

All fields are optional, but normal requests must provide either
`specs` or `file`. Query parameters (`spec`, `channel`, `platform`,
`format`, `filename`) are accepted alongside the body; body fields take
precedence when both are present. `format` is a query-only option.

```bash
curl -sS http://localhost:8000/resolve \
  --json '{"specs":["python=3.12","numpy"],"channels":["conda-forge"],"platforms":["linux-64"]}'
```

Successful responses include:

```text
Location: /r/3a7f...e91b
Cache-Control: public, max-age=86400, immutable
```

#### Raw file upload

Upload an input file directly by setting an appropriate
Content-Type header. No JSON wrapping is needed.

Accepted Content-Types:

- `application/yaml`
- `application/x-yaml`
- `text/yaml`
- `application/toml`
- `text/plain`

```bash
curl -sS --data-binary @environment.yml \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/resolve?platform=linux-64'
```

Use the `filename` query parameter to pick a specific parser when the
Content-Type is ambiguous. For example, `?filename=pixi.lock` forces
the lockfile parser on a generic YAML upload.

Use `POST /transcode` to convert an existing lockfile without solving.

---

### `POST /transcode`

Convert one lockfile format to another without running the solver. The
input must already be a lockfile, and `format` must name a lockfile
exporter such as `conda-lock-v1` or `pixi-lock-v6`.

Query parameters
: `format`
  : Required output format name.

  `platform` (repeatable)
  : Target platform subdir. Defaults to the host platform when omitted.

  `filename`
  : Hint for the parser when uploading a raw file body.

#### JSON body

Send a `TranscodeRequest` object:

```json
{
  "file": "...pixi.lock content...",
  "filename": "pixi.lock",
  "platforms": ["linux-64"]
}
```

#### Raw lockfile upload

Upload a lockfile directly by setting an appropriate Content-Type
header. Use `filename` when the content type does not identify the
lockfile format.

```bash
curl -sS --data-binary @pixi.lock \
  -H 'Content-Type: application/yaml' \
  'http://localhost:8000/transcode?filename=pixi.lock&platform=linux-64&format=conda-lock-v1'
```

The request fails with HTTP 400 and a `reasons` array if the input is
not a lockfile, the output format is not a lockfile, the requested
platforms are missing from the input lockfile, or the request includes
specs or channel overrides that would require solving.

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

The current implementation checks a bounded in-process LRU store first.
`CONDA_PRESTO_RESULT_CACHE_SIZE` caps entry count, and
`CONDA_PRESTO_RESULT_CACHE_MAX_MEMORY_MB` caps retained payload bytes.
When `CONDA_PRESTO_RESULT_CACHE_DIR` or
`CONDA_PRESTO_RESULT_CACHE_REDIS_URL` is set, conda-presto also checks
a file-backed or Redis-backed persistent store using the same
`resolve-v1:<sha256>` CAS key. The hash key includes the normalized
specs, ordered channels, platforms, output format, conda-presto and
solver versions, and metadata from conda's local repodata cache files.
That keeps cached results sensitive to repodata refreshes. A future
sharded repodata index can replace the file metadata marker with exact
shard or sparse-index digests.

---

### `GET /formats`

Returns the list of registered output format names.

```bash
curl http://localhost:8000/formats
```

```json
{
  "formats": [
    "conda-lock-v1",
    "environment-json",
    "environment-yaml",
    "explicit",
    "rattler-lock-v6",
    "requirements"
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
  "platforms": ["linux-32", "linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-32", "win-64"]
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
  "conda-presto": "0.4.0",
  "conda": "26.3.2",
  "conda-rattler-solver": "0.0.6",
  "python": "3.13.3"
}
```

---

### `POST /parse`

Parse an input file and extract its specs and channels without
solving. Useful for validation or for building a UI on top of the
solver.

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

Liveness probe. Returns HTTP 200 with a fixed body.

```json
{"status": "ok"}
```

---

### `GET /`

Interactive API documentation powered by [Scalar UI](https://scalar.com/).
Open this URL in a browser to explore and test the API interactively.

---

### `GET /openapi.json`

The raw OpenAPI 3.1 schema generated by Litestar. This is the same
schema that powers the Scalar UI at `/`.

## Response format

The default response for `/resolve` (both GET and POST) is a JSON
array with one entry per requested platform:

```json
[
  {
    "platform": "linux-64",
    "packages": [
      {
        "name": "zlib",
        "version": "1.3.2",
        "build": "h25fd6f3_2",
        "build_number": 2,
        "channel": "conda-forge",
        "subdir": "linux-64",
        "url": "https://conda.anaconda.org/conda-forge/linux-64/zlib-1.3.2-h25fd6f3_2.conda",
        "sha256": "245c9ee...",
        "md5": "c2a01a08...",
        "size": 95931,
        "depends": ["__glibc >=2.17,<3.0.a0", "libzlib 1.3.2 h25fd6f3_2"],
        "constrains": []
      }
    ],
    "error": null
  }
]
```

Partial failures are expressed as per-platform `error` fields, so a
bad spec for one platform does not fail the whole request.

When `?format=<name>` is set, the response body is the raw exporter
output instead (e.g. an `@EXPLICIT` lockfile or a `pixi.lock` YAML
document). Any solver failure on the exporter path returns HTTP 500
instead of a partial response, because exporters only operate on
successful solves.

## Error responses

| Status | Meaning |
|---:|---|
| 400 | Bad request: invalid specs, unknown format name, or too many specs/platforms |
| 413 | Request body too large (exceeds `CONDA_PRESTO_MAX_BODY_BYTES`) |
| 504 | Solve timed out (exceeds `CONDA_PRESTO_SOLVE_TIMEOUT_S`) |

Error bodies are JSON objects with a `detail` field:

```json
{"detail": "Unknown format 'bogus'. Available: conda-lock-v1, environment-json, ..."}
```

## See also

- [CLI reference](cli.md)
- [Output formats](output-formats.md)
- [Environment variables](environment-variables.md)
