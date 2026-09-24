#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

# Recording prepares this same image before opening the terminal.
if test "${DEMO_DOCKER_BUILT:-0}" != 1; then
    heading 'Build the server image from this checkout'
    docker build --tag conda-presto:docs-demo "$DEMO_REPO" > build.log 2>&1 || {
        cat build.log >&2
        exit 1
    }
else
    heading 'Use the server image built from this checkout before recording'
fi
printf 'Image: conda-presto:docs-demo\n'
pause
heading 'Run the container on a loopback-only random port'
DEMO_CONTAINER=$(docker run --detach --publish 127.0.0.1::8000 \
    --cap-drop ALL --security-opt no-new-privileges \
    --env CONDA_PRESTO_PLATFORMS=linux-64 conda-presto:docs-demo)
printf '$ docker run --detach --publish 127.0.0.1::8000 --cap-drop ALL --security-opt no-new-privileges --env CONDA_PRESTO_PLATFORMS=linux-64 conda-presto:docs-demo\n'
address=$(docker port "$DEMO_CONTAINER" 8000/tcp)
DEMO_URL="http://$address"
for attempt in {1..180}; do
    if curl --fail --silent --max-time 2 "$DEMO_URL/health" > /dev/null; then
        break
    fi
    sleep 1
done
run curl --fail --silent --show-error "$DEMO_URL/health"
save result.json curl --fail --silent --show-error \
    "$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64"
run jq '[.[] | {platform, error, packages: [.packages[].name]}]' result.json
check jq --exit-status 'length == 1 and .[0].error == null and any(.[0].packages[]; .name == "zlib")' result.json
heading 'The container resolved the requested Linux environment'
