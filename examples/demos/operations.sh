#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

heading 'Check readiness, versions and configured capabilities'
start_server
run curl --fail --silent --show-error "$DEMO_URL/health"
save version.json curl --fail --silent --show-error "$DEMO_URL/version"
run jq . version.json
save capabilities.json curl --fail --silent --show-error "$DEMO_URL/capabilities"
run jq . capabilities.json
check jq --exit-status '.sign == false' capabilities.json

heading 'Inspect an invalid format response'
status=$(curl --silent --show-error --output error.json --write-out '%{http_code}' \
    "$DEMO_URL/resolve?spec=zlib&platform=linux-64&format=unknown-demo-format")
printf 'HTTP %s\n' "$status"
test "$status" = 400
run jq '{error, detail}' error.json

heading 'Measure a resolve request and exact retained retrieval'
run curl --fail --silent --show-error --output result.json --dump-header headers.txt \
    --write-out 'Resolve: %{time_total}s\n' \
    "$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64"
location=$(awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' headers.txt)
test -n "$location"
run curl --fail --silent --show-error --output retained.json \
    --write-out 'Retained retrieval: %{time_total}s\n' "$DEMO_URL$location"
run cmp result.json retained.json
heading 'These are local observations, not a service benchmark'
