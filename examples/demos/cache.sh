#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

heading 'Configure a file-backed result cache'
export CONDA_PRESTO_RESULT_CACHE_BACKEND=file
export CONDA_PRESTO_RESULT_CACHE_DIR="$DEMO_WORKDIR/cache"
printf 'CONDA_PRESTO_RESULT_CACHE_BACKEND=file\nCONDA_PRESTO_RESULT_CACHE_DIR=<temporary cache directory>\n'
start_server
save result.json curl --fail --silent --show-error --dump-header headers.txt \
    "$DEMO_URL/resolve?spec=zlib&channel=conda-forge&platform=linux-64"
location=$(awk 'tolower($1) == "location:" {gsub("\r", "", $2); print $2}' headers.txt)
test -n "$location"

heading 'Restart the service with the same cache directory'
stop_server
start_server
heading 'Retrieve the retained result without another solve'
save after-restart.json curl --fail --silent --show-error "$DEMO_URL$location"
run cmp result.json after-restart.json
heading 'Retained bytes survive the service restart'
