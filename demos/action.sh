#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"

cp "$DEMO_REPO/demos/action/conda.toml" conda.toml
cp "$DEMO_REPO/demos/action/client.py" action-client.py
cp "$DEMO_REPO/action.yml" action.yml

heading 'Run the GitHub Action client locally against a real service'
printf 'This executes action.yml Bash steps locally. Hosted runner execution is separate.\n'
start_server
run cat conda.toml
run python action-client.py "$DEMO_URL"
check test -s conda.lock
printf '\nThe Action saved conda.lock and reported solved=true.\n'
