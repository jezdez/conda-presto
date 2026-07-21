# Resolve from the CLI

Use `conda presto` to resolve package specs or environment files without
creating an environment.

## Resolve inline specs

Choose the channel and target platform explicitly when the result will be used
outside the current machine:

```bash
conda presto \
  --channel conda-forge \
  --platform linux-64 \
  python=3.13 numpy
```

Without `--platform`, conda-presto targets the host platform. Conda combines
command-line channels with non-default channels from `.condarc`. If that
effective context has no channels or only `defaults`, conda-presto uses file
channels, then `CONDA_PRESTO_CHANNELS`.

## Resolve an environment file

Pass an environment YAML, requirements file, conda-lock file, or rattler-lock
file selected through conda's environment-specifier registry:

```bash
conda presto --file environment.yml --platform linux-64
```

Specs from repeated `--file` options and positional specs are combined into
one solve:

```bash
conda presto \
  --file environment.yml \
  --file development.yml \
  --platform linux-64 \
  pytest
```

Conda's explicit-file specifier does not expose the lockfile interface used by
this adapter. Explicit files are supported as output, not as `--file` input.

To ignore channels from conda configuration and use only the command-line
channels, pass `--override-channels`:

```bash
conda presto \
  --override-channels \
  --channel conda-forge \
  --file environment.yml
```

## Resolve several platforms

Repeat `--platform` to solve platforms in parallel:

```bash
conda presto \
  --file environment.yml \
  --platform linux-64 \
  --platform osx-arm64 \
  --platform win-64
```

Native JSON contains one result per platform. A platform that cannot be solved
has an `error` value while successful platforms retain their package records.

```bash
conda presto \
  --file environment.yml \
  --platform linux-64 \
  --platform osx-arm64 \
  > solve-result.json
```

## Write an exporter format

Use `--format` to send successful environments through conda's exporter
registry:

```bash
conda presto \
  --file environment.yml \
  --platform linux-64 \
  --platform osx-arm64 \
  --format pixi-lock-v6 \
  > pixi.lock
```

An exporter cannot represent a partial platform failure. The command exits
with an error if any requested platform fails to solve.

List the built-in and plugin-provided formats in
{doc}`../reference/output-formats`.

## Solve from cached repodata only

Pass conda's `--offline` option when the required channel metadata is already
present in the local conda cache:

```bash
conda presto \
  --offline \
  --channel conda-forge \
  --platform linux-64 \
  zlib
```

Offline solving fails when the local repodata cache does not cover the request.
The internal `conda --solver=presto` backend does not support offline mode.

## Next steps

- Use {doc}`transcode-lockfiles` when both the input and output are lockfiles.
- Use {doc}`call-http-api` to make the same resolve operation over HTTP.
- Look up every option and exit code in {doc}`../reference/cli`.
- Adjust target virtual-package defaults through
  {doc}`../reference/environment-variables`.
