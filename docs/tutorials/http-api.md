(demo-http)=
# Resolve and maintain environments over HTTP

```{raw} html
<picture>
  <source srcset="../../http.png" media="(prefers-reduced-motion: reduce)">
  <img class="presto-demo" src="../../http.gif" width="1200" loading="lazy" alt="Resolve inline requirements and uploaded YAML, then retrieve identical retained bytes">
</picture>
```

{download}`Static preview <../../demos/http.png>` · {download}`VHS tape <../../demos/http.tape>`

Follow {doc}`../quickstart` to install and start the server. The HTTP examples use `curl` and `jq`. Workspace steps require the current source described in {doc}`workspaces`.

Resolve inline requirements as native JSON:

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
curl --fail-with-body "$CONDA_PRESTO_URL/health"
curl --fail-with-body "$CONDA_PRESTO_URL/formats" |
  jq '.formats | map(select(startswith("pixi")))'
curl --fail-with-body "$CONDA_PRESTO_URL/resolve" \
  --json '{"specs":["zlib"],"channels":["conda-forge"],"platforms":["linux-64"]}' \
  --output result.json
jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json
```

Expect one Linux result containing `zlib` with `error: null`. HTTP success alone does not rule out a per-platform solver error in native JSON.

Save the shared {download}`environment.yml <../../demos/workspace/environment.yml>` in your working directory. Upload its raw YAML to solve the declared requirements and render a Pixi lock:

```bash
curl --fail-with-body --dump-header headers.txt \
  "$CONDA_PRESTO_URL/resolve?filename=environment.yml&platform=linux-64&format=pixi-lock-v6" \
  --header 'Content-Type: application/yaml' --data-binary @environment.yml \
  --output pixi.lock
```

The YAML declares `zlib` and `zstd` from conda-forge. When the response has a `Location`, retrieve the saved bytes while the entry is retained:

```bash
location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')
test -n "$location"
curl --fail-with-body "$CONDA_PRESTO_URL$location" --output saved.lock
cmp pixi.lock saved.lock
```

A new solve checks channel freshness. Retrieval returns the same bytes until eviction. An absent `Location` means the solve succeeded without retaining its output.

## Export declarations without solving

To change the representation of declared requirements, send the file to `/export`:

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/export?filename=environment.yml&format=requirements" \
  --header 'Content-Type: application/yaml' \
  --data-binary @environment.yml \
  --output requirements.txt
```

The output contains the requested MatchSpecs. This operation does not solve or download packages. Unsolved declarations cannot produce locks, explicit package lists or SBOMs. Workspace manifests use the same export operation, with named selections shown in {doc}`../how-to/parse-workspace`. Saved locks can also be exported, as shown in {doc}`../how-to/extract-workspace-lock`.

## Generate an SBOM

SBOM generation is included in the standard service. Check `/capabilities` and request a document for the target platform:

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/capabilities"
curl --fail-with-body "$CONDA_PRESTO_URL/sbom" \
  --json '{"specs":["zlib"],"platforms":["linux-64"]}' -o sboms.json
jq -j '.sboms[0].content' sboms.json > environment.cdx.json
```

The document describes selected package records. It does not establish which files a downstream product ships. Multiple requested platforms produce separate documents. This request solves the supplied requirements and reuses the registered SBOM exporter. To render selected workspace `conda.lock` records without solving, follow the {doc}`saved-lock SBOM workflow <../how-to/extract-workspace-lock>`.

(demo-http-workspace)=
## Discover and solve a workspace

```{raw} html
<picture>
  <source srcset="../../http-workspace.png" media="(prefers-reduced-motion: reduce)">
  <img class="presto-demo" src="../../http-workspace.gif" width="1200" loading="lazy" alt="Discover workspace targets, export declarations and solve the complete matrix over HTTP">
</picture>
```

{download}`Static preview <../../demos/http-workspace.png>` · {download}`VHS tape <../../demos/http-workspace.tape>`

Save the shared {download}`workspace manifest <../../demos/workspace/conda.toml>` as `conda.toml`. It declares `default` and `tools`, each with `cpu` and `gpu` targets backed by `linux-64`. Both targets declare glibc 2.28, and `gpu` also declares CUDA 12. These are target requirements, independent of the server's hardware.

Submit the manifest as a JSON envelope for discovery:

```bash
jq -n --rawfile file conda.toml \
  '{file: $file, filename: "conda.toml"}' > workspace.json
curl --fail-with-body "$CONDA_PRESTO_URL/parse" \
  --json @workspace.json --output discovery.json
jq '{environments, selected}' discovery.json
```

Discovery lists both environments and returns `selected: []`. Select `tools/gpu` to inspect its composed requirements:

```bash
jq '. + {environments: ["tools"], platforms: ["gpu"]}' workspace.json > selected.json
curl --fail-with-body "$CONDA_PRESTO_URL/parse" \
  --json @selected.json --output selected-result.json
jq '.selected[] | {environment, platform, subdir, specs, system_requirements}' selected-result.json
```

Expect `platform: "gpu"`, `subdir: "linux-64"`, the declared virtual-package versions, and requirements for `zlib` and `zstd`. Select logical target names here because `linux-64` identifies both `cpu` and `gpu`.

Export the selected declarations without solving:

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/export?format=requirements" \
  --json @selected.json --output requirements.txt
cat requirements.txt
```

The output describes the requested dependencies. To obtain exact package records, solve the complete matrix by omitting selectors:

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/resolve?format=conda-workspaces-lock-v1" \
  --json @workspace.json --output conda.lock
```

The returned workspace lock contains all four environment/target selections. Keep this original manifest and lock for validation and selective update.

(demo-http-update)=
## Validate a saved workspace lock

```{raw} html
<picture>
  <source srcset="../../http-update.png" media="(prefers-reduced-motion: reduce)">
  <img class="presto-demo" src="../../http-update.gif" width="1200" loading="lazy" alt="Validate a saved workspace lock, reject changed requirements and update one target over HTTP">
</picture>
```

{download}`Static preview <../../demos/http-update.png>` · {download}`VHS tape <../../demos/http-update.tape>`

Upload the matching `conda.toml` and `conda.lock` from the previous section:

```bash
jq -n --rawfile file conda.lock --rawfile manifest conda.toml \
  '{file: $file, filename: "conda.lock", manifest: $manifest, manifest_filename: "conda.toml"}' \
  > baseline.json
curl --fail-with-body "$CONDA_PRESTO_URL/validate" \
  --json @baseline.json --output consistency.json
jq '{consistent, targets: (.targets | length)}' consistency.json
jq -e '.consistent and (.targets | length == 4)' consistency.json
```

Expect `consistent: true` for all four targets. Validation uses saved metadata and each target's declared virtual packages without solving or downloading packages.

### Reject a changed manifest

Change the uploaded manifest to require `zlib >=99` and check it against the same lock:

```bash
jq '.manifest |= sub(">=1.3,<2"; ">=99")' baseline.json > changed.json
curl --fail-with-body "$CONDA_PRESTO_URL/validate" --json @changed.json \
  --write-out 'HTTP %{http_code}\n' --output mismatch.json
jq '{consistent, reason: .targets[0].reason}' mismatch.json
```

The report contains `consistent: false` and a reason for each affected target. HTTP 200 means the check completed, so inspect `.consistent`. Malformed or unsupported files produce HTTP 400, and a parser timeout produces HTTP 504.

Validation always checks the complete workspace and rejects environment or platform selectors. It does not check for newer packages or vulnerabilities. Its response is not retained in the artifact cache.

### Update one target

Use the original matching manifest and lock to update `zstd` in `tools/cpu`:

```bash
jq '. + {environment: "tools", platform: "cpu", packages: ["zstd"]}' \
  baseline.json > update.json
curl --fail-with-body "$CONDA_PRESTO_URL/update" \
  --json @update.json --output updated.lock
```

The response is a complete workspace lock. The `zstd >=1.5,<2` constraint still applies. Transitive dependencies in `tools/cpu` may change, while `default/cpu`, `default/gpu` and `tools/gpu` keep their package selections. The provider can retain the current version when no suitable update is selected.

In the source environment containing Presto, compare the unselected package references:

```bash
python - <<'PYTHON'
from pathlib import Path
from ruamel.yaml import YAML

reader = YAML(typ="safe")
before = reader.load(Path("conda.lock").read_text())
after = reader.load(Path("updated.lock").read_text())
for environment, data in before["environments"].items():
    for target, packages in data["packages"].items():
        if (environment, target) != ("tools", "cpu"):
            assert after["environments"][environment]["packages"][target] == packages
print("Unselected package references are unchanged")
PYTHON
```

Check all four targets in the returned lock:

```bash
jq --rawfile file updated.lock '.file = $file' baseline.json > updated.json
curl --fail-with-body "$CONDA_PRESTO_URL/validate" \
  --json @updated.json --output updated-check.json
jq -e '.consistent and (.targets | length == 4)' updated-check.json
```

The baseline must satisfy every manifest target before updating, and Presto checks the complete result before returning it. A changed manifest, missing target or unsupported input fails instead of triggering a full relock. Keep `conda.lock` while reviewing `updated.lock`. A failed update returns no partial lock.

## Sign and verify the saved bytes

Signing and verification support are included. Enable signing with noninteractive identity credentials and a trust configuration, as described in {doc}`../reference/configuration`. Then sign the retained document:

```bash
key=$(jq -r '.sboms[0].location // empty | split("/")[-1]' sboms.json)
test -n "$key"
jq -n --arg key "$key" '{key:$key}' > sign-request.json
curl --fail-with-body "$CONDA_PRESTO_URL/sign" \
  --json @sign-request.json -o signed.json
jq -j '.bundle' signed.json > environment.cdx.sigstore.json
```

Choose `EXPECTED_SIGNER` and `EXPECTED_ISSUER` from your approved deployment configuration. Do not derive trust from the bundle you are checking.

```bash
: "${EXPECTED_SIGNER:?Set the approved signer identity}"
: "${EXPECTED_ISSUER:?Set the approved identity issuer}"
jq -n --rawfile artifact environment.cdx.json \
  --rawfile bundle environment.cdx.sigstore.json \
  --arg name "$(jq -r '.artifact_name' signed.json)" \
  --arg identity "$EXPECTED_SIGNER" --arg issuer "$EXPECTED_ISSUER" \
  '{artifact:($artifact|@base64),bundle:$bundle,artifact_name:$name,
    expected_identity:$identity,expected_issuer:$issuer}' > verify-request.json
curl --fail-with-body "$CONDA_PRESTO_URL/verify" --json @verify-request.json
```

Changing even one artifact byte or supplying a different signer pair makes verification fail. The signature authenticates saved output. It does not establish how the original solve was constructed. Archive the exact artifact, bundle and approved trust configuration outside the result cache when you need long-term evidence.

See {doc}`../reference/http-api` for request fields, error handling and the meaning of each verification result.
