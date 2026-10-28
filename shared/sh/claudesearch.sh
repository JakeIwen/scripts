#!/usr/bin/env bash

# Search Claude Code conversation events, optionally grouping matches by session.

set -u -o pipefail

source "$(dirname "${BASH_SOURCE[0]}")/session_search_header.sh"

if ! session_search_parse_args "$@"; then
    session_search_usage claudesearch >&2
    exit 2
fi
if [ "$session_search_help" = true ]; then
    session_search_usage claudesearch
    exit 0
fi

for claudesearch_dependency in grep awk python3; do
    if ! command -v "$claudesearch_dependency" >/dev/null 2>&1; then
        echo "claudesearch: required command not found: $claudesearch_dependency" >&2
        exit 127
    fi
done

claudesearch_root=${CLAUDESEARCH_ROOT:-${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}}
claudesearch_projects="$claudesearch_root/projects"

if [ ! -d "$claudesearch_projects" ]; then
    echo "claudesearch: projects directory not found: $claudesearch_projects" >&2
    exit 1
fi

session_search_files claude "$claudesearch_root" | session_search_extract
