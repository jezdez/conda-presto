# Resolve and save an output

Follow {doc}`../quickstart` to install and start the server. This example resolves zlib for Linux, saves a lockfile and retrieves the same bytes from the cache.

```bash
export CONDA_PRESTO_URL=http://127.0.0.1:8000
curl --fail-with-body "$CONDA_PRESTO_URL/health"
curl --fail-with-body -D headers.txt \
  "$CONDA_PRESTO_URL/resolve?format=pixi-lock-v6" \
  --json '{"specs":["zlib"],"platforms":["linux-64"]}' \
  -o pixi.lock
```

When the response has a `Location`, retrieve it while the entry is retained:

```bash
location=$(awk 'tolower($1) == "location:" {print $2}' headers.txt | tr -d '\r')
test -n "$location"
curl --fail-with-body "$CONDA_PRESTO_URL$location" -o saved.lock
cmp pixi.lock saved.lock
```

A new solve checks channel freshness. Retrieval returns the saved bytes until eviction. An absent `Location` means the solve succeeded without retaining its output.

## Export declarations without solving

To change the representation of declared requirements, send the file to `/export`:

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/export?filename=environment.yml&format=requirements" \
  --header 'Content-Type: application/yaml' \
  --data-binary $'channels:\n  - conda-forge\ndependencies:\n  - python=3.13\n  - zlib\n' \
  --output requirements.txt
```

The output contains the requested MatchSpecs. This operation does not solve or download packages. Unsolved declarations cannot produce locks, explicit package lists or SBOMs. Workspace manifests use the same export operation, with named selections shown in {doc}`../how-to/parse-workspace`. Saved locks can also be exported, as shown in {doc}`../how-to/extract-workspace-lock`.

## Generate an SBOM

Use a server with the optional providers installed, as described in {doc}`../how-to/run-with-docker`. Check `/capabilities` first.

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/capabilities"
curl --fail-with-body "$CONDA_PRESTO_URL/sbom" \
  --json '{"specs":["zlib"],"platforms":["linux-64"]}' -o sboms.json
jq -j '.sboms[0].content' sboms.json > environment.cdx.json
```

The document describes selected package records. It does not establish which files a downstream product ships. Multiple requested platforms produce separate documents. This request solves the supplied requirements and reuses the registered SBOM exporter. To render selected workspace `conda.lock` records without solving, follow the {doc}`saved-lock SBOM workflow <../how-to/extract-workspace-lock>`.

## Check a lock after changing its manifest

Start with the matching `conda.toml` and workspace `conda.lock` from {doc}`../how-to/parse-workspace`. Upload both files:

```bash
jq -n --rawfile file conda.lock --rawfile manifest conda.toml \
  '{file: $file, filename: "conda.lock", manifest: $manifest, manifest_filename: "conda.toml"}' |
  curl --fail-with-body "$CONDA_PRESTO_URL/validate" \
    --header 'Content-Type: application/json' --data-binary @- \
    --output consistency.json
jq '.consistent' consistency.json
```

The result is `true` when every declared environment and target passes. Checking uses saved metadata without solving or downloading packages. It includes named variants on the same conda platform, independently of the server host.

The example manifest requires Python 3.13. Create a changed copy that instead requires Python 3.12 and check it against the same lock:

```bash
sed 's/3\.13\.\*/3.12.*/' conda.toml > changed-conda.toml
jq -n --rawfile file conda.lock --rawfile manifest changed-conda.toml \
  '{file: $file, filename: "conda.lock", manifest: $manifest, manifest_filename: "conda.toml"}' |
  curl --fail-with-body "$CONDA_PRESTO_URL/validate" \
    --header 'Content-Type: application/json' --data-binary @- \
    --output mismatch.json
jq '.targets[] | select(.consistent == false)' mismatch.json
```

The response reports `consistent: false` and a reason for each affected target. HTTP 200 means the check completed, so automation should inspect `.consistent` rather than rely only on the HTTP status. For example, `jq -e '.consistent' consistency.json` succeeds only for a consistent result. Malformed or unsupported files produce HTTP 400, and a parser timeout produces HTTP 504.

This operation checks the complete workspace and does not accept environment or platform selectors. It does not check repository freshness, download or verify archives, or evaluate vulnerability policy. The response is not retained in the artifact cache.

## Update one target from a saved lock

Use the original matching `conda.toml` and `conda.lock` from {doc}`../how-to/parse-workspace`. Update the direct `pytest` dependency in `test/linux-64`:

```bash
jq -n --rawfile file conda.lock --rawfile manifest conda.toml \
  '{file: $file, filename: "conda.lock", manifest: $manifest, manifest_filename: "conda.toml",
    environment: "test", platform: "linux-64", packages: ["pytest"]}' |
  curl --fail-with-body "$CONDA_PRESTO_URL/update" \
    --header 'Content-Type: application/json' --data-binary @- \
    --output updated.lock
```

The response is a complete workspace lock. The manifest's `pytest >=8` constraint still applies. Dependencies inside the selected target may also change, while the saved selections for `default` and `test/osx-arm64` remain unchanged. The provider can keep the current version when no suitable update is selected.

In the conda environment containing Presto, compare the unselected package references:

```bash
python - <<'PY'
from pathlib import Path
from ruamel.yaml import YAML

reader = YAML(typ="safe")
before = reader.load(Path("conda.lock").read_text())
after = reader.load(Path("updated.lock").read_text())
for environment, data in before["environments"].items():
    for target, packages in data["packages"].items():
        if (environment, target) != ("test", "linux-64"):
            assert after["environments"][environment]["packages"][target] == packages
print("Unselected package references are unchanged")
PY
```

The baseline must satisfy every manifest target before an update starts. A changed manifest, missing target or unsupported input fails instead of triggering a full relock. Leave `conda.lock` in place while inspecting `updated.lock`. If updating fails, no partial lock is returned and previously retained outputs remain available.

## Sign and verify the saved bytes

On a deployment with signing deliberately enabled and noninteractive credentials configured:

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
