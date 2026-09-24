# Sign and verify a retained output

Sign an artifact already retained by this deployment, then verify its exact bytes against an identity approved by the recipient. The signature describes a later output-signing step. It does not record the original solve inputs.

This recipe makes live OIDC, certificate-authority and transparency-log requests. For verification without signing credentials, use the {doc}`offline demonstration </demos/index>`.

## Supply an unattended identity

Run the service inside a GitHub Actions job with conda-presto's server dependencies, Python, `curl` and `jq` installed. Run the shell blocks below in the same Bash session or workflow `run` step. From a source checkout with Pixi installed, save the shell commands as `signing-example.sh` and run `pixi run --locked -e prod bash signing-example.sh`.

Grant the job these permissions:

```yaml
permissions:
  contents: read
  id-token: write
```

GitHub supplies `GITHUB_ACTIONS`, `ACTIONS_ID_TOKEN_REQUEST_URL` and `ACTIONS_ID_TOKEN_REQUEST_TOKEN` to the job. The service process must inherit them. Sigstore's credential detector requests an OIDC token with audience `sigstore`, without browser login. A client running in Actions does not give a separately deployed server an identity. See [GitHub's OIDC permissions and request variables](https://docs.github.com/en/actions/reference/security/oidc).

GitLab CI is another supported environment. Its job must supply `GITLAB_CI` and a `SIGSTORE_ID_TOKEN` issued with audience `sigstore`, for example through `id_tokens: {SIGSTORE_ID_TOKEN: {aud: sigstore}}`. A standalone `SIGSTORE_ID_TOKEN` outside a supported environment is not a generic token override for Presto. See the [credential detector's supported environments](https://pypi.org/project/id/).

## Choose signing services and trust

For public Sigstore, set:

```bash
set -euo pipefail
export CONDA_PRESTO_SIGSTORE_SIGNING_ENABLED=true
export CONDA_PRESTO_SIGSTORE_ALLOW_PUBLIC_SIGNING=true
export CONDA_PRESTO_SIGSTORE_OFFLINE=false
unset CONDA_PRESTO_SIGSTORE_TRUST_CONFIG
```

This selects Sigstore's production trust configuration and publishes signer identity and artifact metadata to public services.

For a custom deployment, replace the public-signing choice with:

```bash
export CONDA_PRESTO_SIGSTORE_ALLOW_PUBLIC_SIGNING=false
export CONDA_PRESTO_SIGSTORE_TRUST_CONFIG=/run/presto/client-trust.json
```

Supply that JSON file from the deployment operator. It must be a Sigstore `ClientTrustConfig` containing both `trustedRoot` and `signingConfig`, with the intended certificate-authority and transparency-log services. A bare trusted-root document is insufficient for signing. The configured certificate authority must accept the job's issuer and the `sigstore` audience. Presto uses the same file for verification.

## Start the service and retain an artifact

```bash
export CONDA_PRESTO_CHANNELS=conda-forge
export CONDA_PRESTO_ALLOWED_CHANNELS=conda-forge
export CONDA_PRESTO_PLATFORMS=linux-64
export CONDA_PRESTO_SOLVE_TIMEOUT_S=180
conda-presto --serve --host 127.0.0.1 --port 8000 >presto.log 2>&1 &
presto_pid=$!
trap 'kill "$presto_pid"' EXIT
base=http://127.0.0.1:8000
curl --fail --silent --show-error --retry 60 --retry-delay 1 \
  --retry-connrefused "$base/health"
curl --fail --silent --show-error "$base/capabilities" |
  jq -e '.sign and .verify'
curl --fail --silent --show-error --get "$base/resolve" \
  --data-urlencode spec=python=3.13 \
  --data-urlencode platform=linux-64 \
  --dump-header resolve.headers --output artifact.json
location=$(python - <<'PY'
from pathlib import Path

for line in Path("resolve.headers").read_text().splitlines():
    if line.lower().startswith("location:"):
        print(line.split(":", 1)[1].strip())
        break
else:
    raise SystemExit("No retained output. Check cache configuration and metadata freshness.")
PY
)
```

The `sign` capability reports enabled configuration and installed provider APIs. The signing request checks whether credentials and remote services actually work. Keep the listener private. See {doc}`deploy-securely` before exposing it to other clients.

## Sign and verify

Sign the retained key and save the returned bundle:

```bash
jq -n --arg key "${location##*/}" '{key: $key}' |
  curl --fail --silent --show-error "$base/sign" \
    -H 'Content-Type: application/json' --data-binary @- >signed.json
jq -r '.bundle' signed.json >artifact.sigstore.json
```

Choose the expected signer and issuer from the recipient's policy, independently of the supplied bundle. For a GitHub workflow on `main`, replace `OWNER`, `REPOSITORY` and `sign.yml` below with the approved workflow. The ref must match the signing run. [Sigstore documents the GitHub issuer and workflow identity](https://docs.sigstore.dev/certificate_authority/oidc-in-fulcio/).

```bash
expected_identity='https://github.com/OWNER/REPOSITORY/.github/workflows/sign.yml@refs/heads/main'
expected_issuer='https://token.actions.githubusercontent.com'
jq -n --rawfile artifact artifact.json --slurpfile signed signed.json \
  --arg identity "$expected_identity" --arg issuer "$expected_issuer" \
  --arg name "result-${location##*/}" \
  '{artifact: ($artifact | @base64), bundle: $signed[0].bundle,
    artifact_name: $name, expected_identity: $identity,
    expected_issuer: $issuer}' |
  curl --fail --silent --show-error "$base/verify" \
    -H 'Content-Type: application/json' --data-binary @- |
  jq -e '.signature_verified and .artifact_verified and .signer_verified and .claims_checked'
```

This example encodes the native UTF-8 JSON response without reformatting it. Verification succeeds only when the signature, exact artifact bytes, subject name and expected signer pair match. `claims_checked` confirms Presto's recognized output-signing statement shape. Save `artifact.json` and `artifact.sigstore.json` together because retained URLs can expire or be evicted.
