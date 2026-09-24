#!/usr/bin/env bash
# Sourced by VHS before displaying the workflow.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
DEMO_NAME=$1
export PS1="$DEMO_PS1"
unset NO_COLOR
# Bash writes prompts to stderr, so normalize only the CLI's diagnostics.
conda-presto() {
    local status=0
    command conda-presto "$@" 2> "$DEMO_WORKDIR/command.stderr" || status=$?
    python "$DEMO_REPO/demos/normalize.py" < "$DEMO_WORKDIR/command.stderr" >&2
    return "$status"
}

case "$DEMO_NAME" in
    cli)
        cp "$DEMO_REPO/demos/workspace/environment.yml" environment.yml
        ;;
    workspace|locks)
        cp "$DEMO_REPO/demos/workspace/conda.toml" conda.toml
        cp "$DEMO_REPO/demos/workspace/check_update.py" check_update.py
        cp "$DEMO_REPO/demos/workspace/check_locks.py" check_locks.py
        if test "$DEMO_NAME" = locks; then
            conda-presto -f conda.toml --format conda-workspaces-lock-v1 > conda.lock
            conda-presto -c conda-forge -p linux-64 zlib --format conda-lock-v1 > conda-lock.yml
        fi
        ;;
    http|operations)
        start_server
        ;;
    cache|docker)
        ;;
    action)
        cp "$DEMO_REPO/demos/action/conda.toml" conda.toml
        cp "$DEMO_REPO/demos/action/client.py" action-client.py
        cp "$DEMO_REPO/demos/action/workflow.yml" workflow.yml
        cp "$DEMO_REPO/action.yml" action.yml
        start_server
        ;;
    trust)
        cp "$DEMO_REPO/tests/fixtures/sigstore/artifact.txt" artifact.txt
        cp "$DEMO_REPO/demos/trust/verify.py" verify.py
        cp "$DEMO_REPO/demos/trust/policy.json" policy.json
        cp "$DEMO_REPO/tests/fixtures/sigstore/bundle.sigstore.json" bundle.sigstore.json
        cp "$DEMO_REPO/tests/fixtures/sigstore/trust.json" trust.json
        ;;
    *) return 2 ;;
esac

demo_finish() {
    cleanup
    trap - EXIT
    printf '0\n' > "$DEMO_REPO/demos/$DEMO_NAME.status"
    PS1="DEMO_COMPLETE$ "
}
