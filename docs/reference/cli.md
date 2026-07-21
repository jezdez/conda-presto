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
: Additional channel to search. Can be repeated. Conda also includes channels
  from `.condarc` unless `--override-channels` is present. When the effective
  conda context has no channels or only `defaults`, conda-presto uses channels
  from input files, then `CONDA_PRESTO_CHANNELS`.

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
: Path to an environment file. Can be repeated. Each file is selected and
  parsed through conda's installed environment-specifier plugins. The base
  installation supports environment YAML and requirements files, while the
  required conda-lockfiles package adds conda-lock and rattler-lock formats.
  The adapter does not accept conda's explicit-file specifier as input. Specs
  from all files are merged with positional specs.

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

`-O`, `--override-channels`
: Ignore channels configured in `.condarc` and use only the channels
  given via `-c`.

`--solver`
: Standard conda parser option. Direct `conda presto` resolves always use
  rattler through conda-rattler-solver, so this option does not select a
  different engine for the subcommand. To delegate a conda transaction to the
  broker-backed Presto solver, use `conda --solver=presto ...` instead.

`--use-local`
: Add conda's local build channel. Equivalent to `-c local`.

`--repodata-fn`
: Select a repodata filename. Can be repeated. Conda adds `repodata.json` as a
  final fallback.

`--experimental {jlap,lock}`
: Deprecated option inherited from conda's networking parser. Both choices are
  no longer supported and the option has no conda-presto-specific behavior.

`--repodata-use-zst`, `--no-repodata-use-zst`
: Enable or disable compressed `repodata.json.zst` metadata.

`--repodata-use-shards`, `--no-repodata-use-shards`
: Enable or disable sharded repodata where a channel provides it.

`-C`, `--use-index-cache`
: Accept cached channel metadata even when its normal time-to-live has expired.

`-k`, `--insecure`
: Disable TLS certificate verification for channel access.

`--no-lock`
: Disable conda locking while reading or updating the repodata cache. Avoid
  this when another conda process can use the same package cache.

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

Convert one lockfile format to another without solving:

```bash
conda presto -f pixi.lock -p linux-64 --format conda-lock-v1 > conda-lock.yml
```

Start the HTTP server on a custom port:

```bash
conda presto --serve --host 0.0.0.0 --port 9000
```

Use the broker-backed Presto solver for a conda transaction:

```bash
conda create --dry-run --solver presto -n demo -c conda-forge numpy
```

Offline solve using only cached repodata:

```bash
conda presto --offline -c conda-forge -p linux-64 zlib
```

## Exit codes

| Code | Meaning |
|---:|---|
| 0 | Command completed. Default JSON output may still contain per-platform `error` fields. |
| 1 | Invalid input, unknown format, or solver failure on an exporter-format path. |
| 2 | Argument parsing error from `argparse`. |

## See also

- {doc}`http-api`
- {doc}`output-formats`
- {doc}`solver-backend`
- {doc}`environment-variables`
