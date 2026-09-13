# Resolve from the CLI

Use the one-shot command when conda-presto is installed locally:

```bash
conda presto -c conda-forge -p linux-64 python=3.13 numpy
conda presto -f environment.yml -p linux-64 -p osx-arm64 \
  --format rattler-lock-v6 > pixi.lock
```

The default output is native JSON with a result or error for each platform. Exporter output requires successful solves for every selected platform. The command selects packages without creating or changing a prefix.

To convert a covered lockfile without solving:

```bash
conda presto -f pixi.lock -p linux-64 --format conda-lock-v1 > conda-lock.yml
```

CLI conversion uses the parser's normal package-record materialization. Use the HTTP {doc}`transcode-lockfiles` operation when package archives must not be fetched.

See {doc}`../reference/cli` for flags and exit codes and {doc}`../reference/output-formats` for format limitations.
