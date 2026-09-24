#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
start_server

heading 'Discover workspace environments without solving'
save workspace.json jq -n --rawfile file conda.toml \
    '{file:$file,filename:"conda.toml"}'
save discovery.json curl --fail --silent --show-error \
    "$DEMO_URL/parse" --json @workspace.json
run jq -c '.environments[] | {name, platforms}' discovery.json
run jq '.selected' discovery.json
check jq -e '.selected == [] and ([.environments[].name] | sort) == ["default", "tools"] and all(.environments[]; .platforms == {cpu: "linux-64", gpu: "linux-64"})' discovery.json

heading 'Select tools/gpu with its declared system requirements'
save selected.json jq '. + {environments:["tools"],platforms:["gpu"]}' workspace.json
save selected-result.json curl --fail --silent --show-error \
    "$DEMO_URL/parse" --json @selected.json
run jq '.selected[0] | {environment, platform, subdir, specs, system_requirements}' selected-result.json
check jq -e '.selected | length == 1 and .[0].environment == "tools" and .[0].platform == "gpu" and .[0].subdir == "linux-64" and .[0].system_requirements.glibc == "2.28" and .[0].system_requirements.cuda == "12"' selected-result.json

heading 'Export the selected requirements without solving'
save requirements.txt curl --fail --silent --show-error \
    "$DEMO_URL/export?format=requirements" --json @selected.json
run cat requirements.txt
check python -c 'from pathlib import Path; from conda.models.match_spec import MatchSpec; specs = [MatchSpec(line) for line in Path("requirements.txt").read_text().splitlines() if line and not line.startswith("#")]; assert {spec.name: str(spec.version) for spec in specs} == {"zlib": ">=1.3,<2", "zstd": ">=1.5,<2"}'

heading 'Solve the complete workspace into one lock'
save conda.lock curl --fail --silent --show-error \
    "$DEMO_URL/resolve?format=conda-workspaces-lock-v1" --json @workspace.json
save lock.json jq -n --rawfile file conda.lock '{file:$file,filename:"conda.lock"}'
save saved.json curl --fail --silent --show-error "$DEMO_URL/parse" --json @lock.json
run jq -c '.environments[] | {name, platforms}' saved.json
check jq -e '.format == "conda-workspaces-lock-v1" and .selected == [] and ([.environments[].name] | sort) == ["default", "tools"] and all(.environments[]; .platforms == {cpu: "linux-64", gpu: "linux-64"})' saved.json
heading 'The lock contains both environments and both named targets'
