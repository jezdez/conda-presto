#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
cp "$DEMO_REPO/demos/workspace/check_locks.py" check_locks.py
conda presto -f conda.toml --format conda-workspaces-lock-v1 > conda.lock
conda presto -c conda-forge -p linux-64 zlib --format conda-lock-v1 > conda-lock.yml

heading "Inspect and extract saved tools/cpu records"
save saved.json conda presto --parse -f conda.lock
run jq '{environments, selected}' saved.json

mkdir extracted
save extracted/conda.lock conda presto --export -f conda.lock -e tools -p cpu --format conda-workspaces-lock-v1
save extracted.json conda presto --parse -f extracted/conda.lock
check jq -e '.environments == [{name: "tools", platforms: {cpu: "linux-64"}}]' extracted.json
run jq '.environments' extracted.json

heading "Render exact URLs and a normalized declaration"
save explicit.txt conda presto --export -f conda.lock -e tools -p cpu --format explicit
run cat explicit.txt
save normalized.toml conda presto --export -f conda.lock -e tools -p cpu --format conda-toml
run head -n 6 normalized.toml

heading "Convert a generic lock and generate an SBOM from saved records"
save pixi.lock conda presto --export -f conda-lock.yml -p linux-64 --format pixi-lock-v6
save sbom.json conda presto --export -f conda.lock -e tools -p cpu --manifest conda.toml --format cyclonedx-json-v1.7
run jq '{bomFormat, specVersion, packages: [.components[] | {name, version}], roots: [.metadata.component.properties[] | select(.name == "conda:environment:root-dependency-source")]}' sbom.json
run python check_locks.py
