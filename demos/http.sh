#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

heading 'Start a local HTTP service'
start_server
run curl --fail --silent --show-error "$DEMO_URL/health"

heading 'Solve zlib for Linux and inspect the result'
save result.json curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    --data '{"specs":["zlib"],"channels":["conda-forge"],"platforms":["linux-64"]}' \
    "$DEMO_URL/resolve"
run jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json
check jq --exit-status 'length == 1 and .[0].error == null and any(.[0].packages[]; .name == "zlib")' result.json

heading 'Export a lock and retrieve its exact retained bytes'
save pixi.lock curl --fail --silent --show-error --dump-header headers.txt \
    "$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64&format=pixi-lock-v6"
location=$(awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' headers.txt)
test -n "$location"
save retained.lock curl --fail --silent --show-error "$DEMO_URL$location"
run cmp pixi.lock retained.lock
heading 'The retrieved lock is byte-for-byte identical'
