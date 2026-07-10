# How conda-presto works

conda-presto is a solve-only bridge between conda input formats and conda
output formats. It reads specs or environment files, resolves fully pinned
package records for one or more platforms, and writes JSON or a conda exporter
format. It does not create prefixes, link packages, or install anything.

## Data flow

```{mermaid}
flowchart LR
    A["Request\n(specs or file)"] --> B["Input parser\n(env-spec plugin)"]
    B --> C{"Lockfile fast path?"}
    C -->|"yes"| D["Reuse package records\nfrom input lockfile"]
    C -->|"no"| E["Solve\n(conda-rattler-solver)"]
    E --> F["Result cache\n/r/<hash>"]
    D --> G["Exporter or\nnative JSON"]
    F --> G
    G --> H["CLI stdout or\nHTTP response"]
```

The `/transcode` endpoint and the CLI lockfile-to-lockfile path take the fast
branch only when the input is already a lockfile, the requested platforms are
present in that lockfile, the requested output format is also a lockfile, and
the request does not add specs or override channels. Everything else is a solve.

## Input

Input parsing is delegated to conda's env-spec plugin registry. That keeps
conda-presto out of the business of hand-parsing every file format. Installed
env-spec plugins decide how to read files such as:

- `environment.yml`
- `pixi.toml`
- `pyproject.toml`
- `requirements.txt`
- `conda-lock.yml`
- `pixi.lock`

Inline command-line specs and HTTP query/body specs skip file parsing and go
straight into the solve request.

## Inspecting environments

The inspection endpoints separate questions that can be answered from input
alone from questions that need selected package records. `/preflight` parses
and applies deterministic local checks without contacting a channel or running
the solver. A successful preflight therefore says that the input is well
formed; it does not establish that the environment is satisfiable.

`/diff` and `/explain` operate on resolved packages. Diff compares two
environments per platform and can reuse records from a lockfile that already
covers the requested platform; other inputs require a solve. Explain is
single-platform because it traces dependency edges from the requested specs to
one selected package. Its traversal is bounded, and `complete: false` signals
that the local package metadata could not account for every edge.

## Solving

Solving is handled by `conda-rattler-solver`. For multi-platform requests,
conda-presto dispatches each target platform through a persistent
`ProcessPoolExecutor`. Each worker can retain its own warm repodata/index state
across requests.

Cross-platform solving relies on virtual package injection. When solving for a
foreign target such as `linux-64` from macOS, conda-presto sets the target
subdir and virtual package values (`__glibc`, `__linux`, `__osx`, `__win`) on
conda's context before constructing the solver input.

The default HTTP and CLI output path returns lightweight `msgspec.Struct`
objects. The exporter path returns conda `Environment` objects because conda
exporter plugins consume that model directly.

## Output

Without `--format` or `?format=`, conda-presto emits native JSON: one
`SolveResult` per requested platform, each containing resolved package records
or a per-platform error string.

With `--format` or `?format=`, conda-presto routes successful solved
environments through conda's exporter plugin registry. This exposes built-in
formats such as `explicit` and plugin-provided formats such as `conda-lock-v1`
and `pixi-lock-v6` without separate output implementations.

## Caching

conda-presto has three caching layers:

On-disk repodata cache
: conda's standard cache for channel metadata. It is shared with other conda
  tools and expires according to conda's repodata TTL settings.

In-memory solver index cache
: a `RattlerIndexHelper` is cached by `(channels, platform)` inside each
  process. Repeated solves for the same channel/platform pair skip index
  construction and pay mostly SAT solving time.

Content-addressed result cache
: successful HTTP `/resolve` responses are stored under a SHA-256 key and
  returned with `Location: /r/<hash>` when retained. The key includes normalized
  specs, ordered channels, target platforms, output format, relevant dependency
  versions, and markers for conda's local repodata cache files. Repodata
  refreshes therefore create new keys instead of reusing stale solve results.
  The in-process LRU can be backed by Litestar file or Redis stores.

## HTTP layer

The HTTP API is a Litestar app served by uvicorn. The server adds compression,
CORS handling, rate limiting, request body limits, a solve timeout, startup
cache warmup, a health endpoint, OpenAPI, and interactive API documentation.

The server is optional. The same core parsing, solving, transcode, and export
helpers are used by the `conda presto` CLI path.

## Plugin integration

conda-presto registers as a conda subcommand. Normal one-shot use is:

```bash
conda presto -f environment.yml -p linux-64 --format pixi-lock-v6
```

Server use is:

```bash
conda presto --serve
```

The project also relies on conda's env-spec and exporter plugin registries, so
new parser or exporter plugins become available without conda-presto-specific
registration code.
