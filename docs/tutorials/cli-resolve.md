# Create a multi-platform lockfile

This tutorial starts with a small `environment.yml`, resolves it for Linux and
macOS, and writes a `pixi.lock`. Along the way, it shows the difference between
conda-presto's native result and an exporter result.

## Before you start

Install conda-presto with {doc}`../quickstart`, then verify the subcommand:

```bash
conda presto --help
```

Create a working directory containing this `environment.yml`:

```yaml
name: science
channels:
  - conda-forge
dependencies:
  - python=3.13
  - numpy
  - pandas
```

conda-presto reads this file through conda's environment specifier registry.
It does not create the `science` environment.

## Inspect the native solve result

First resolve only Linux and keep the native JSON:

```bash
conda presto -f environment.yml -p linux-64 > linux-result.json
```

Confirm that the solve succeeded:

```bash
jq '.[0] | {platform, package_count: (.packages | length), error}' linux-result.json
```

The result has one platform, a nonzero package count, and an `error` value of
`null`. Native JSON can represent a failure for one platform while preserving
successful results for the others.

## Add another platform

Request Linux and Apple silicon macOS:

```bash
conda presto -f environment.yml \
  -p linux-64 \
  -p osx-arm64 > multi-platform-result.json
```

Inspect the result order and package counts:

```bash
jq -r '.[] | "\(.platform): \(.packages | length) packages"' multi-platform-result.json
```

The output order follows the `-p` arguments even though the solves may run in
parallel.

## Write a pixi.lock

Run the same solve through the `pixi-lock-v6` exporter:

```bash
conda presto -f environment.yml \
  -p linux-64 \
  -p osx-arm64 \
  --format pixi-lock-v6 > pixi.lock
```

Exporter output is a single document, so every requested platform must solve
successfully. The native JSON path is the better diagnostic form when you need
per-platform errors.

Confirm that the lockfile contains both platforms:

```bash
rg 'linux-64|osx-arm64' pixi.lock
```

## Reuse the lockfile

conda-presto can read the lockfile through the same conda plugin system:

```bash
conda presto -f pixi.lock \
  -p linux-64 \
  --format conda-lock-v1 > conda-lock.yml
```

Because the requested platform is already present and both formats are
lockfiles, this conversion reuses package records without solving again.

## What you learned

- `conda presto` resolves without installing packages.
- Native JSON keeps one result per platform and can represent partial failure.
- `--format` uses conda exporter plugins and requires successful environments.
- Lockfile-to-lockfile conversion can skip solving when the input covers every
  requested platform.

For individual CLI tasks, see {doc}`../how-to/resolve-from-cli` and
{doc}`../how-to/transcode-lockfiles`. Exact flags are in
{doc}`../reference/cli`.
