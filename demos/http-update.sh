#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
cp "$DEMO_REPO/demos/workspace/check_update.py" check_update.py
conda presto -f conda.toml --format conda-workspaces-lock-v1 > conda.lock
start_server

heading 'Validate the complete saved lock against its manifest'
save baseline.json jq -n --rawfile file conda.lock --rawfile manifest conda.toml \
    '{file:$file,filename:"conda.lock",manifest:$manifest,manifest_filename:"conda.toml"}'
save consistency.json curl --fail --silent --show-error \
    "$DEMO_URL/validate" --json @baseline.json
run jq '{consistent, targets:(.targets | length)}' consistency.json
check jq -e '.consistent and (.targets | length == 4) and all(.targets[]; .consistent and .reason == null)' consistency.json

heading 'A changed manifest returns a mismatch with HTTP 200'
save changed.json jq '.manifest |= sub(">=1.3,<2"; ">=99")' baseline.json
save mismatch-status.txt curl --fail --silent --show-error \
    "$DEMO_URL/validate" --json @changed.json \
    --write-out 'HTTP %{http_code}\n' --output mismatch.json
run cat mismatch-status.txt
check grep -Fx 'HTTP 200' mismatch-status.txt
run jq '{consistent, reason:.targets[0].reason}' mismatch.json
check jq -e '.consistent == false and (.targets | length == 4) and all(.targets[]; .consistent == false and (.reason | length > 0))' mismatch.json

heading 'Update tools/cpu using the original consistent baseline'
save update.json jq '. + {environment:"tools",platform:"cpu",packages:["zstd"]}' baseline.json
save updated.lock curl --fail --silent --show-error \
    "$DEMO_URL/update" --json @update.json
run python check_update.py conda.lock updated.lock

heading 'Validate all four targets in the returned lock'
save updated.json jq --rawfile file updated.lock '.file = $file' baseline.json
save updated-check.json curl --fail --silent --show-error \
    "$DEMO_URL/validate" --json @updated.json
run jq '{consistent, targets:(.targets | length)}' updated-check.json
check jq -e '.consistent and (.targets | length == 4) and all(.targets[]; .consistent)' updated-check.json
