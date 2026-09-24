#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/workspace/environment.yml" environment.yml
start_server

heading 'Discover the installed Pixi output formats'
save formats.json curl --fail --silent --show-error "$DEMO_URL/formats"
run jq '.formats | map(select(startswith("pixi")))' formats.json
check jq --exit-status '.formats | index("pixi-lock-v6") != null' formats.json

heading 'Solve zlib for Linux and inspect the result'
save result.json curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    --data '{"specs":["zlib"],"channels":["conda-forge"],"platforms":["linux-64"]}' \
    "$DEMO_URL/resolve"
run jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json
check jq --exit-status 'length == 1 and .[0].error == null and any(.[0].packages[]; .name == "zlib")' result.json

heading 'Upload an environment file and save a Pixi lock'
run cat environment.yml
save pixi.lock curl --fail --silent --show-error --dump-header headers.txt \
    -H 'Content-Type: application/yaml' --data-binary @environment.yml \
    "$DEMO_URL/resolve?filename=environment.yml&platform=linux-64&format=pixi-lock-v6"
check test -s pixi.lock
run head -n 8 pixi.lock

heading 'Retrieve the exact output from its retained location'
location=$(awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' headers.txt)
test -n "$location"
run grep -i '^location:' headers.txt
save retained.lock curl --fail --silent --show-error "$DEMO_URL$location"
run cmp pixi.lock retained.lock
heading 'The retrieved lock is byte-for-byte identical'
