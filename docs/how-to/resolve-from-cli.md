(demo-cli)=
# Resolve from the CLI

```{raw} html
<picture>
  <source srcset="../../cli.png" media="(prefers-reduced-motion: reduce)">
  <img class="presto-demo" src="../../cli.gif" width="1200" loading="lazy" alt="Resolve package requirements and export declarations in the terminal">
</picture>
```

{download}`Static preview <../../demos/cli.png>` · {download}`VHS tape <../../demos/cli.tape>`

Use the one-shot command when conda-presto is installed locally. Save the shared {download}`environment.yml <../../demos/workspace/environment.yml>` and {download}`extra-deps.yml <../../demos/workspace/extra-deps.yml>` fixtures in the current directory:

```bash
conda presto -c conda-forge -p linux-64 zlib
conda presto -f environment.yml -p linux-64 -p osx-arm64 \
  > environment.json
```

The default output is native JSON with a result or error for each platform. Exporter output requires successful solves for every selected platform. The command selects packages without creating or changing a prefix.

To merge both files and an inline requirement into one solve:

```bash
conda presto -f environment.yml -f extra-deps.yml -p linux-64 bzip2 \
  > merged.json
```

The result includes `zlib` and `zstd` from the first file, `xz` from the second, and the inline `bzip2` requirement.

To save exact package URLs for a later `conda create --file` invocation:

```bash
conda presto -f environment.yml -p linux-64 --format explicit > explicit.txt
```

The output starts with `@EXPLICIT` after any comment headers. Presto writes the package selection without installing it.

To render declared dependencies without solving:

```bash
conda presto --export -f environment.yml --format requirements > requirements.txt
```

This preserves supported declarations without producing a pinned lock. Workspace manifests support selected environment and target export through the same mode, as shown in {doc}`parse-workspace`.

To convert a covered lockfile without solving or downloading packages:

```bash
conda presto -f environment.yml -p linux-64 --format pixi-lock-v6 > pixi.lock
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
