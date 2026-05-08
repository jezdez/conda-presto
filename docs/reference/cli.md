# CLI reference

conda-presto registers as a conda subcommand plugin. After installation,
`conda presto` is available alongside other conda commands.

## Synopsis

```text
conda presto [OPTIONS] [SPECS...]
```

Resolve package specs or environment files to fully pinned package lists
without installing anything.

## Positional arguments

`SPECS`
: One or more conda match specs, e.g. `python=3.12 numpy`. These are
  combined with any specs found in files passed via `--file`.

## Options

`-c`, `--channel`
: Channel to search. Can be repeated to search multiple channels in
  priority order. When omitted, falls back to
  `CONDA_PRESTO_CHANNELS` (default: `conda-forge`).

  ```bash
  conda presto -c conda-forge -c bioconda python=3.12
  ```

`-p`, `--platform`
: Target platform subdir (e.g. `linux-64`, `osx-arm64`, `win-64`).
  Can be repeated to solve for multiple platforms in parallel. When
  omitted, solves for the current host platform only.

  ```bash
  conda presto -p linux-64 -p osx-arm64 python=3.12
  ```

`-f`, `--file`
: Path to an environment file. Accepts `.yml`, `.yaml`, `.toml`,
  `.txt`, `.lock`, and `.json` files. Can be repeated; specs from all
  files are merged into a single solve together with any positional
  specs.

  ```bash
  conda presto -f environment.yml -f extra-deps.yml -p linux-64
  ```

`--format`
: Route the output through a conda exporter plugin instead of
  emitting the default JSON. See
  [Output formats](output-formats.md) for the full list.

  ```bash
  conda presto --format explicit -c conda-forge -p linux-64 zlib
  ```

`--override-channels`
: Ignore channels configured in `.condarc` and use only the channels
  given via `-c`.

`--solver`
: Solver backend to use. Default: `rattler` (via `conda-rattler-solver`).
  Can also be set globally with `CONDA_SOLVER`.

`--offline`
: Run without network access. Only packages already present in the
  local repodata cache are considered.

`--serve`
: Start the HTTP API server instead of solving. Combine with `--host`
  and `--port` to configure the bind address.

  ```bash
  conda presto --serve --port 9000
  ```

`--host`
: Bind address for the HTTP server. Default: the value of
  `CONDA_PRESTO_HOST`, or `127.0.0.1`.

`--port`
: Port for the HTTP server. Default: the value of
  `CONDA_PRESTO_PORT`, or `8000`.

## Examples

Resolve a single package for one platform:

```bash
conda presto -c conda-forge -p linux-64 zlib
```

Resolve an environment file for multiple platforms:

```bash
conda presto -f environment.yml -p linux-64 -p osx-arm64
```

Merge inline specs with a file and produce an explicit lockfile:

```bash
conda presto -f environment.yml -p linux-64 --format explicit scipy
```

Convert `environment.yml` to `pixi.lock`:

```bash
conda presto -f environment.yml -p linux-64 --format pixi-lock-v6 > pixi.lock
```

Start the HTTP server on a custom port:

```bash
conda presto --serve --host 0.0.0.0 --port 9000
```

Use a specific solver backend:

```bash
conda presto --solver libmamba -c conda-forge -p linux-64 numpy
```

Offline solve using only cached repodata:

```bash
conda presto --offline -c conda-forge -p linux-64 zlib
```

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Solve succeeded for all requested platforms |
| 1 | One or more platforms failed to solve (partial failure) |
| 2 | Argument error or invalid input |

## See also

- [HTTP API reference](http-api.md)
- [Output formats](output-formats.md)
- [Environment variables](environment-variables.md)
