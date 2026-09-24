#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/tests/fixtures/sigstore/artifact.txt" artifact.txt
cp "$DEMO_REPO/examples/demos/trust/verify.py" verify.py
cp "$DEMO_REPO/examples/demos/trust/policy.json" policy.json
cp "$DEMO_REPO/tests/fixtures/sigstore/bundle.sigstore.json" bundle.sigstore.json
cp "$DEMO_REPO/tests/fixtures/sigstore/trust.json" trust.json

heading 'Verify a public Sigstore fixture offline through Presto'
printf 'The expected signer and staging trust are fixed for this test fixture.\n'
run python verify.py artifact.txt

heading 'Changing one byte invalidates the artifact match'
run cp artifact.txt tampered.txt
run bash -c 'printf "\n" >> tampered.txt'
run python verify.py tampered.txt --expect-error artifact-mismatch

heading 'A valid signature still requires the expected signer'
run python verify.py artifact.txt \
    --identity https://example.org/different-workflow \
    --expect-error untrusted-identity
printf '\nNo signing credentials or network access are needed. Provenance claims remain unchecked.\n'
