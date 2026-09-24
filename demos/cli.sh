#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/environment.yml" environment.yml

heading "Resolve inline specs as native JSON"
save specs.json conda-presto -c conda-forge -p linux-64 zlib
check jq -e 'length == 1 and .[0].platform == "linux-64" and .[0].error == null and any(.[0].packages[]; .name == "zlib")' specs.json
run jq '[.[] | {platform, packages: [.packages[].name], error}]' specs.json

heading "Resolve an environment file through the conda subcommand"
run cat environment.yml
save environment.json conda presto -f environment.yml -p linux-64
check jq -e 'length == 1 and .[0].error == null and ([.[0].packages[].name] | contains(["zlib", "zstd"]))' environment.json
run jq '[.[] | {platform, packages: [.packages[].name], error}]' environment.json

heading "Normalize declarations without solving"
save requirements.txt conda-presto --export -f environment.yml --format requirements
check python -c '
from pathlib import Path
from conda.models.match_spec import MatchSpec
specs = [MatchSpec(line) for line in Path("requirements.txt").read_text().splitlines() if line and not line.startswith("#")]
assert {spec.name: str(spec.version) for spec in specs} == {"zlib": ">=1.3,<2", "zstd": ">=1.5,<2"}
'
run cat requirements.txt
