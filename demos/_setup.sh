#!/usr/bin/env bash
# Sourced by VHS before displaying the workflow.
set -euo pipefail

DEMO_REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEMO_WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/conda-presto-demo.XXXXXXXX")
DEMO_SERVER_PID=

# Ignore deployment settings so examples use only their own service and stores.
for variable in ${!CONDA_PRESTO_@}; do
    unset "$variable"
done

cleanup() {
    if test -n "$DEMO_SERVER_PID"; then
        kill "$DEMO_SERVER_PID" 2>/dev/null || true
        wait "$DEMO_SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$DEMO_WORKDIR"
}
trap cleanup EXIT

start_server() {
    DEMO_PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
    DEMO_URL="http://127.0.0.1:$DEMO_PORT"
    export DEMO_URL
    CONDA_PRESTO_PLATFORMS=linux-64 \
    CONDA_PRESTO_CHANNELS=conda-forge \
    CONDA_PRESTO_PERSISTENT_WORKER=true \
    CONDA_PRESTO_SOLVE_TIMEOUT_S=120 \
    CONDA_NO_LOCK=false \
        command conda presto --serve --host 127.0.0.1 --port "$DEMO_PORT" > server.log 2>&1 &
    DEMO_SERVER_PID=$!
    local attempt
    for attempt in {1..180}; do
        if curl --fail --silent --max-time 2 "$DEMO_URL/health" > /dev/null; then
            return
        fi
        if ! kill -0 "$DEMO_SERVER_PID" 2>/dev/null; then
            cat server.log >&2
            return 1
        fi
        sleep 1
    done
    printf 'The demo service did not become ready.\n' >&2
    cat server.log >&2
    return 1
}

cd "$DEMO_WORKDIR"
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
