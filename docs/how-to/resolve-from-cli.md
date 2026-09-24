# Resolve from the CLI

Watch the {ref}`demo-cli` demo or run its checked-in script.

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

To update a declared direct dependency in one workspace target, keep the manifest unchanged and provide the complete saved lock:

```bash
mkdir -p updated
conda presto --update -f conda.lock --manifest conda.toml \
  -e test -p linux-64 numpy > updated/conda.lock
conda presto --validate -f updated/conda.lock --manifest conda.toml
```

Use the exact environment and logical target declared by your manifest. This updates `numpy` only in `test/linux-64` and preserves saved package selections for every other environment and target. The solver may also change transitive dependencies in the selected target. Presto rejects an inconsistent baseline and writes the complete result only after its final consistency check succeeds. Redirect to a different file so the shell does not truncate your baseline before Presto reads it.

Compare the saved package references for every unselected target:

```bash
python - <<'PY'
import json
from pathlib import Path
from conda.common.serialize.yaml import loads

before, after = [loads(Path(name).read_text()) for name in ("conda.lock", "updated/conda.lock")]
for environment, entry in before["environments"].items():
    for target, references in entry["packages"].items():
        if (environment, target) != ("test", "linux-64"):
            updated = after["environments"][environment]["packages"][target]
            references = sorted(json.dumps(ref, sort_keys=True) for ref in references)
            updated = sorted(json.dumps(ref, sort_keys=True) for ref in updated)
            assert references == updated
print("Unselected package references are unchanged")
PY
```

See {doc}`../reference/cli` for flags and exit codes and {doc}`../reference/output-formats` for format limitations.
