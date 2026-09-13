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

## Generate an SBOM

Use a server with the optional providers installed, as described in {doc}`../how-to/run-with-docker`. Check `/capabilities` first.

```bash
curl --fail-with-body "$CONDA_PRESTO_URL/capabilities"
curl --fail-with-body "$CONDA_PRESTO_URL/sbom" \
  --json '{"specs":["zlib"],"platforms":["linux-64"]}' -o sboms.json
jq -j '.sboms[0].content' sboms.json > environment.cdx.json
```

The document describes selected package records. It does not establish which files a downstream product ships. Multiple requested platforms produce separate documents.

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
