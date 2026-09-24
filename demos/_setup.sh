#!/usr/bin/env bash
# Sourced by VHS before displaying the workflow.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
DEMO_NAME=$1
export PS1="$DEMO_PS1"
unset NO_COLOR
# Bash writes prompts to stderr, so normalize only the CLI's diagnostics.
conda() {
    local status=0
    command conda "$@" 2> "$DEMO_WORKDIR/command.stderr" || status=$?
    python "$DEMO_REPO/demos/normalize.py" < "$DEMO_WORKDIR/command.stderr" >&2
    return "$status"
}

case "$DEMO_NAME" in
    cli)
        cp "$DEMO_REPO/demos/workspace/environment.yml" environment.yml
        cp "$DEMO_REPO/demos/workspace/extra-deps.yml" extra-deps.yml
        ;;
    workspace|locks)
        cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
        cp "$DEMO_REPO/demos/workspace/check_update.py" check_update.py
        cp "$DEMO_REPO/demos/workspace/check_locks.py" check_locks.py
        if test "$DEMO_NAME" = locks; then
            conda presto -f conda.toml --format conda-workspaces-lock-v1 > conda.lock
            conda presto -c conda-forge -p linux-64 zlib --format conda-lock-v1 > conda-lock.yml
        fi
        ;;
    http)
        cp "$DEMO_REPO/demos/workspace/environment.yml" environment.yml
        start_server
        ;;
    http-workspace|http-update)
        cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
        if test "$DEMO_NAME" = http-update; then
            cp "$DEMO_REPO/demos/workspace/check_update.py" check_update.py
            conda presto -f conda.toml --format conda-workspaces-lock-v1 > conda.lock
        fi
        start_server
        ;;
    *) return 2 ;;
esac

demo_finish() {
    cleanup
    trap - EXIT
    printf '0\n' > "$DEMO_REPO/demos/$DEMO_NAME.status"
    PS1="DEMO_COMPLETE$ "
}
