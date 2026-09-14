# Resolve from the CLI

Use the one-shot command when conda-presto is installed locally:

```bash
conda presto -c conda-forge -p linux-64 python=3.13 numpy
conda presto -f environment.yml -p linux-64 -p osx-arm64 \
  --format rattler-lock-v6 > pixi.lock
```

The default output is native JSON with a result or error for each platform. Exporter output requires successful solves for every selected platform. The command selects packages without creating or changing a prefix.

To render declared dependencies without solving:

```bash
conda presto --export -f environment.yml --format requirements > requirements.txt
```

This preserves supported declarations without producing a pinned lock. Workspace manifests support selected environment and target export through the same mode, as shown in {doc}`parse-workspace`.

To convert a covered lockfile without solving or downloading packages:

```bash
conda presto --export -f pixi.lock -p linux-64 --format conda-lock-v1 > conda-lock.yml
```

Export mode rejects inputs and selections the output format cannot represent. It does not fall back to a solve. See {doc}`transcode-lockfiles` for ordinary lock conversion restrictions and {doc}`extract-workspace-lock` for named workspace lock extraction.

See {doc}`../reference/cli` for flags and exit codes and {doc}`../reference/output-formats` for format limitations.
