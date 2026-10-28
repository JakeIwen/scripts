#!/usr/bin/env bash

# Search Codex JSONL events, optionally grouping matches by session.

set -u -o pipefail

source "$(dirname "${BASH_SOURCE[0]}")/session_search_header.sh"

if ! session_search_parse_args "$@"; then
    session_search_usage codexsearch >&2
    exit 2
fi
if [ "$session_search_help" = true ]; then
    session_search_usage codexsearch
    exit 0
fi

for codexsearch_dependency in grep awk python3; do
    if ! command -v "$codexsearch_dependency" >/dev/null 2>&1; then
        echo "codexsearch: required command not found: $codexsearch_dependency" >&2
        exit 127
    fi
done

codexsearch_root=${CODEXSEARCH_ROOT:-"$HOME/.codex"}
codexsearch_sessions="$codexsearch_root/sessions"

if [ ! -d "$codexsearch_sessions" ]; then
    echo "codexsearch: sessions directory not found: $codexsearch_sessions" >&2
    exit 1
fi

session_search_files codex "$codexsearch_root" | session_search_extract
