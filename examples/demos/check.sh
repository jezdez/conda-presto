#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

if test "$#" = 0; then
    set -- cli workspace locks http cache trust action operations
fi
for demo in "$@"; do
    case "$demo" in
        cli|workspace|locks|http|cache|trust|action|docker|operations) ;;
        *) printf 'Unknown demo: %s\n' "$demo" >&2; exit 2 ;;
    esac
    bash "examples/demos/$demo.sh"
    printf '\n%s demo passed\n' "$demo"
done
