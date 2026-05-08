# How conda-presto works

conda-presto is a solver and format bridge, not an installer. It connects
conda's env-spec plugins (input parsers) to conda's exporter plugins (output
formatters) via a solve step. You hand it specs or environment files, it
resolves fully pinned packages for one or more platforms, and it emits results
as JSON or any conda exporter format. Nothing is installed.

## Data flow

```{mermaid}
flowchart LR
    A["Input\n(env file / specs)"] --> B["Parser\n(env-spec plugin)"]
    B --> C["Solver\n(rattler)"]
    C --> D["Exporter\n(format plugin)"]
    D --> E["Output\n(JSON / lockfile / etc.)"]
```

## Input

conda-presto accepts any format that conda's env-spec plugins understand:

- `environment.yml`
- `pixi.toml`
- `pyproject.toml`
- `requirements.txt`
- `conda-lock` / `pixi-lock`
- inline specs on the command line

Because input parsing is delegated to env-spec plugins, any new format that
gets a plugin automatically works with conda-presto.

## Processing

Solving is handled by conda-rattler-solver, a SAT-based solver. For
multi-platform solves, conda-presto fans out across platforms using a
`ProcessPoolExecutor`, running each platform solve in its own process.

Cross-platform solving relies on automatic virtual package injection. When
solving for a foreign platform (say, `linux-64` on a macOS host), conda-presto
injects the appropriate virtual packages (`__glibc`, `__linux`, `__osx`,
`__win`) so the solver sees the same constraints a native machine would.

## Output

The default output is native JSON using the `SolveResult` model. When you
request a different format, conda-presto routes the result through conda's
exporter plugins. Any exporter plugin installed in the environment is available,
so adding new output formats is a matter of installing the right plugin.

## Caching

Two layers of caching keep solves fast:

On-disk repodata cache
: conda's standard repodata cache with TTL-based expiration. This is shared
  with the rest of conda, so if you have recently run `conda install`, the
  repodata is already warm.

In-memory index cache
: A `RattlerIndexHelper` instance is cached in memory, keyed by
  `(channels, platform)`. The first solve for a given channel/platform pair pays
  roughly 700 ms to build the index. Subsequent solves with the same key pay
  only SAT solving time.

## HTTP layer

conda-presto can run as an HTTP server using Litestar and uvicorn. The server
adds:

- Brotli and gzip response compression
- CORS headers for browser clients
- Rate limiting per endpoint
- Request size limits to prevent abuse

The server is optional. You can use conda-presto purely as a CLI tool or call
its Python API directly.

## Plugin integration

conda-presto registers as a conda subcommand via the standard plugin system.
It can run standalone (`conda presto resolve ...`) or as a long-lived HTTP
server (`conda presto serve`). The plugin hooks into conda's env-spec and
exporter plugin registries, so it benefits from any plugins already installed
in the environment.
