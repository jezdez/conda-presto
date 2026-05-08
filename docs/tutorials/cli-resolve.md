# CLI resolve

This tutorial walks through the `conda presto` subcommand, from
basic one-shot solves to multi-platform lockfile pipelines.

## Prerequisites

Install conda-presto globally with [pixi](https://pixi.sh):

```bash
pixi global install --git https://github.com/jezdez/conda-presto.git
```

Verify the installation:

```bash
conda presto --help
```

## Basic resolve

The simplest invocation takes inline specs, a channel, and a target
platform:

```bash
conda presto -c conda-forge -p linux-64 python=3.12 numpy
```

The output is a JSON array with one entry per platform. Each entry
contains the full dependency closure: package names, versions, builds,
URLs, SHA256 hashes, and dependency metadata.

You can pass as many specs as you like:

```bash
conda presto -c conda-forge -p linux-64 python=3.12 scipy pandas matplotlib
```

## Resolving from an environment file

Instead of inline specs, pass an environment file with `-f`:

```bash
conda presto -f environment.yml -p linux-64
```

Any format that conda's env-spec plugins understand works here,
including `environment.yml`, `pixi.toml`, `pyproject.toml`,
`requirements.txt`, `conda-lock.yml`, and `pixi.lock`.

You can also merge multiple files into a single solve:

```bash
conda presto -f environment.yml -f extra-deps.yml -p linux-64
```

## Multi-platform solves

Pass multiple `-p` flags to solve for several platforms at once.
The solves run in parallel:

```bash
conda presto -f environment.yml -p linux-64 -p osx-arm64
```

The JSON output contains one entry per platform:

```text
[
  {"platform": "linux-64", "packages": [...], "error": null},
  {"platform": "osx-arm64", "packages": [...], "error": null}
]
```

If the solve fails on one platform but succeeds on another, the
failed platform carries an `"error"` string while the others still
return their results.

## Output formats

By default, conda-presto emits its native JSON format. Pass
`--format` to route the output through conda's exporter plugins
instead.

### Explicit lockfile

The `@EXPLICIT` format is a plain text file with one URL per line,
suitable for `conda create --file`:

```bash
conda presto -c conda-forge -p linux-64 --format explicit python=3.12 numpy
```

### conda-lock

Generate a `conda-lock.yml` file (v1 format):

```bash
conda presto -f environment.yml -p linux-64 -p osx-arm64 --format conda-lock-v1 > conda-lock.yml
```

### pixi.lock

Generate a `pixi.lock` file (rattler-lock v6):

```bash
conda presto -f environment.yml -p linux-64 -p osx-arm64 --format pixi-lock-v6 > pixi.lock
```

### All available formats

The following formats are available out of the box via `conda-lockfiles`:

| Format name | Aliases | Description |
|---|---|---|
| `explicit` | | `@EXPLICIT` URL-per-line lockfile |
| `environment-yaml` | `yaml`, `yml`, `env.yml` | `environment.yml` |
| `environment-json` | `json` | JSON environment spec |
| `requirements` | `reqs`, `txt` | pip-style requirements |
| `conda-lock-v1` | | `conda-lock.yml` |
| `rattler-lock-v6` | `pixi-lock-v6` | `pixi.lock` |

Any additional formats registered by other exporter plugins are
picked up automatically.

## Pipeline: environment.yml to pixi.lock

Because conda-presto reads any format conda understands and writes
any exporter format, you can pipe them together into a lockfile
conversion workflow:

```bash
conda presto -f environment.yml --format pixi-lock-v6 > pixi.lock
conda env create -n demo -f pixi.lock
```

The second step uses `conda-lockfiles`' env-spec loader, so `pixi.lock`
is just another input format to conda. The same pattern works for
any pair of formats:

```bash
# pyproject.toml to conda-lock.yml
conda presto -f pyproject.toml -p linux-64 --format conda-lock-v1 > conda-lock.yml

# requirements.txt to explicit lockfile
conda presto -f requirements.txt -c conda-forge -p linux-64 --format explicit > lockfile.txt
```

```{tip}
When using `--format`, a solver failure on any platform raises the
whole command because exporters only operate on successful solves.
Drop `--format` when you want per-platform partial results in JSON.
```

## Cross-platform virtual packages

When solving for a foreign platform (for example, solving for
`linux-64` from macOS), conda needs virtual packages like `__glibc`,
`__linux`, and `__osx` to be present for the target platform.
conda-presto injects sensible defaults automatically:

| Platform | Virtual packages | Defaults |
|---|---|---|
| linux | `__glibc`, `__linux` | 2.17, 5.15 |
| osx | `__osx` | 11.0 |
| win | `__win` | 0 |

Override these via environment variables if your target environment
needs different versions:

```bash
CONDA_PRESTO_GLIBC_VERSION=2.28 conda presto -c conda-forge -p linux-64 python=3.12
```

See the [environment variables reference](../index) for the full list.
