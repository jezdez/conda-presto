#!/usr/bin/env bash

DEMO_REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEMO_WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/conda-presto-demo.XXXXXXXX")
DEMO_SERVER_PID=
DEMO_PAUSE=${DEMO_PAUSE:-0}

# Ignore deployment settings so examples use only their own service and stores.
for variable in ${!CONDA_PRESTO_@}; do
    unset "$variable"
done

pause() {
    if test "$DEMO_PAUSE" != 0; then
        sleep "$DEMO_PAUSE"
    fi
}

heading() {
    printf '\n%s\n' "$*"
    pause
}

run() {
    printf '\n'
    python -c 'import shlex, sys; print("$ " + shlex.join(sys.argv[1:]))' "$@"
    local status=0
    "$@" || status=$?
    pause
    return "$status"
}

save() {
    local destination=$1
    shift
    printf '\n'
    python -c 'import shlex, sys; print("$ " + shlex.join(sys.argv[2:]) + " > " + shlex.quote(sys.argv[1]))' "$destination" "$@"
    local status=0
    "$@" > "$destination" || status=$?
    pause
    return "$status"
}

check() {
    "$@" > /dev/null
}

stop_server() {
    if test -n "$DEMO_SERVER_PID"; then
        kill "$DEMO_SERVER_PID" 2>/dev/null || true
        wait "$DEMO_SERVER_PID" 2>/dev/null || true
        DEMO_SERVER_PID=
    fi
}

cleanup() {
    stop_server
    if test -n "${DEMO_CONTAINER:-}"; then
        docker rm --force "$DEMO_CONTAINER" > /dev/null 2>&1 || true
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
