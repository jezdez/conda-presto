#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/environment.yml" environment.yml
cp "$DEMO_REPO/demos/workspace/extra-deps.yml" extra-deps.yml

heading "Resolve inline specs as native JSON"
save specs.json conda presto -c conda-forge -p linux-64 zlib
check jq -e 'length == 1 and .[0].platform == "linux-64" and .[0].error == null and any(.[0].packages[]; .name == "zlib")' specs.json
run jq '[.[] | {platform, packages: [.packages[].name], error}]' specs.json

heading "Resolve an environment file for Linux and macOS"
run cat environment.yml
save environment.json conda presto -f environment.yml -p linux-64 -p osx-arm64
check jq -e 'length == 2 and ([.[].platform] | sort) == ["linux-64", "osx-arm64"] and all(.[]; .error == null and ([.packages[].name] | contains(["zlib", "zstd"])))' environment.json
run jq -c '.[] | {platform, packages: (.packages | length), error}' environment.json

heading "Merge two environment files and an inline requirement"
run cat extra-deps.yml
save merged.json conda presto -f environment.yml -f extra-deps.yml -p linux-64 bzip2
check jq -e 'length == 1 and .[0].platform == "linux-64" and .[0].error == null and ([.[0].packages[].name] | contains(["zlib", "zstd", "xz", "bzip2"]))' merged.json
run jq -r '[.[0].packages[].name] | sort | join(", ")' merged.json

heading "Export exact package URLs for conda create --file"
save explicit.txt conda presto -f environment.yml -p linux-64 --format explicit
check python -c '
from pathlib import Path
from urllib.parse import urlsplit
lines = Path("explicit.txt").read_text().splitlines()
assert "@EXPLICIT" in lines
urls = [line for line in lines if line and not line.startswith(("#", "@"))]
assert urls and all(urlsplit(url).scheme == "https" for url in urls)
assert any("/zlib-" in url for url in urls)
assert any("/zstd-" in url for url in urls)
'
run sed -n '/^@EXPLICIT/,$p' explicit.txt
heading "Install later with: conda create -n demo --file explicit.txt"

heading "Normalize declarations without solving"
save requirements.txt conda presto --export -f environment.yml --format requirements
check python -c '
from pathlib import Path
from conda.models.match_spec import MatchSpec
specs = [MatchSpec(line) for line in Path("requirements.txt").read_text().splitlines() if line and not line.startswith("#")]
assert {spec.name: str(spec.version) for spec in specs} == {"zlib": ">=1.3,<2", "zstd": ">=1.5,<2"}
'
run cat requirements.txt
