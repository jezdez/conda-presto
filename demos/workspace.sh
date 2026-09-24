#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
cp "$DEMO_REPO/demos/workspace/check_update.py" check_update.py
heading "Discover two environments and named Linux targets"
run cat conda.toml

save discovery.json conda presto --parse -f conda.toml
check jq -e '.selected == [] and ([.environments[].name] | sort) == ["default", "tools"] and all(.environments[]; .platforms == {cpu: "linux-64", gpu: "linux-64"})' discovery.json
run jq '{environments, selected}' discovery.json

heading "Select tools/gpu with declared glibc and CUDA versions"
save selected.json conda presto --parse -f conda.toml -e tools -p gpu
check jq -e '.selected | length == 1 and .[0].environment == "tools" and .[0].platform == "gpu" and .[0].subdir == "linux-64" and .[0].system_requirements.glibc == "2.28" and .[0].system_requirements.cuda == "12"' selected.json
run jq '.selected[] | {environment, platform, subdir, specs, system_requirements}' selected.json

heading "Solve and validate all four environment/target combinations"
save conda.lock conda presto -f conda.toml --format conda-workspaces-lock-v1
save consistency.json conda presto --validate -f conda.lock --manifest conda.toml
check jq -e '.consistent and (.targets | length == 4) and all(.targets[]; .consistent and .subdir == "linux-64" and .reason == null) and ([.targets[] | [.environment, .platform]] | sort) == [["default", "cpu"], ["default", "gpu"], ["tools", "cpu"], ["tools", "gpu"]]' consistency.json
run jq '{consistent, targets: [.targets[] | {environment, platform, consistent}]}' consistency.json

heading "Reject a manifest that no longer matches the saved lock"
mkdir changed
sed 's/zlib = ">=1.3,<2"/zlib = ">=99"/' conda.toml > changed/conda.toml
status=0
save mismatch.json conda presto --validate -f conda.lock --manifest changed/conda.toml || status=$?
check test "$status" -eq 1
check jq -e '.consistent == false and (.targets | length == 4) and all(.targets[]; .consistent == false and (.reason | length > 0))' mismatch.json
run jq '{consistent, reason: .targets[0].reason}' mismatch.json

heading "Update tools/cpu while keeping the other three selections"
save updated.lock conda presto --update -f conda.lock --manifest conda.toml -e tools -p cpu zstd
run python check_update.py conda.lock updated.lock
mkdir updated
cp updated.lock updated/conda.lock
save updated-check.json conda presto --validate -f updated/conda.lock --manifest conda.toml
check jq -e '.consistent and (.targets | length == 4)' updated-check.json
run jq '{consistent, targets: (.targets | length)}' updated-check.json
