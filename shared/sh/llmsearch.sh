#!/usr/bin/env bash

set -u -o pipefail

llmsearch_scripts=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$llmsearch_scripts/session_search_header.sh"

if ! session_search_parse_args "$@"; then
    session_search_usage llmsearch >&2
    exit 2
fi
if [ "$session_search_help" = true ]; then
    session_search_usage llmsearch
    exit 0
fi

llmsearch_claude=${CLAUDESEARCH_ROOT:-${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}}
llmsearch_codex=${CODEXSEARCH_ROOT:-"$HOME/.codex"}
if [ ! -d "$llmsearch_claude/projects" ] && [ ! -d "$llmsearch_codex/sessions" ]; then
    echo 'llmsearch: no Claude or Codex session directories found' >&2
    exit 1
fi

llmsearch_sources() {
    local result=0
    if [ -d "$llmsearch_claude/projects" ]; then
        "$llmsearch_scripts/claudesearch.sh" -- "$session_search_query" || result=$?
    fi
    if [ -d "$llmsearch_codex/sessions" ]; then
        "$llmsearch_scripts/codexsearch.sh" -- "$session_search_query" || result=$?
    fi
    return "$result"
}

if [ "$session_search_group" = true ]; then
    SESSION_SEARCH_RECORDS=1 llmsearch_sources |
        jq -nr -L "$llmsearch_scripts" '
            include "session_search_records"; session_search_group_directories
        '
else
    llmsearch_sources
fi
