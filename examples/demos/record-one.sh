#!/usr/bin/env bash
set -uo pipefail

name=$1
bash "examples/demos/$name.sh" 2>&1 |
    python -u examples/demos/normalize.py |
    tee "docs/_static/demos/$name.txt"
status=$?
printf '%s\n' "$status" > "docs/_static/demos/$name.status"
if test "$status" = 0; then
    printf '\nDEMO COMPLETE\n'
else
    printf '\nDEMO FAILED\n'
fi
exit "$status"
