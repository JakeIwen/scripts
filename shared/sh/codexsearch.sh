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

for codexsearch_dependency in grep jq python3; do
    if ! command -v "$codexsearch_dependency" >/dev/null 2>&1; then
        echo "codexsearch: required command not found: $codexsearch_dependency" >&2
        exit 127
    fi
done

codexsearch_query=$session_search_query
codexsearch_root=${CODEXSEARCH_ROOT:-"$HOME/.codex"}
codexsearch_sessions="$codexsearch_root/sessions"
codexsearch_index="$codexsearch_root/session_index.jsonl"

if [ ! -d "$codexsearch_sessions" ]; then
    echo "codexsearch: sessions directory not found: $codexsearch_sessions" >&2
    exit 1
fi

excerpt() {
    awk -v query="$codexsearch_query" '
        BEGIN {
            text = ""
        }
        {
            if (text != "") text = text " "
            text = text $0
        }
        END {
            gsub(/[[:space:]]+/, " ", text)
            lower_text = tolower(text)
            lower_query = tolower(query)
            match_at = index(lower_text, lower_query)
            if (!match_at) exit

            start_at = match_at - 50
            if (start_at < 1) start_at = 1
            end_at = match_at + length(query) + 49
            if (end_at > length(text)) end_at = length(text)
            excerpt_length = end_at - start_at + 1

            result = substr(text, start_at, excerpt_length)
            if (start_at > 1) result = "..." result
            if (start_at + excerpt_length - 1 < length(text)) result = result "..."
            print result
        }
    '
}

session_search_files codex "$codexsearch_root" |
while IFS= read -r -d '' codexsearch_provider && IFS= read -r -d '' codexsearch_file; do
    codexsearch_meta=$(jq -c 'select(.type == "session_meta") | .payload' \
        "$codexsearch_file" 2>/dev/null | sed -n '1p')
    codexsearch_id=$(printf '%s\n' "$codexsearch_meta" |
        jq -r '.session_id // .id // empty' 2>/dev/null)
    codexsearch_cwd=$(printf '%s\n' "$codexsearch_meta" |
        jq -r '.cwd // empty' 2>/dev/null)

    if [ -z "$codexsearch_id" ]; then
        codexsearch_id=$(basename "$codexsearch_file" .jsonl | sed -E \
            's/^.*-([[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12})$/\1/')
    fi

    codexsearch_name=""
    if [ -f "$codexsearch_index" ]; then
        codexsearch_name=$(jq -r --arg id "$codexsearch_id" \
            'select(.id == $id) | .thread_name // empty' \
            "$codexsearch_index" 2>/dev/null | tail -n 1)
    fi
    if [ -z "$codexsearch_name" ] && [ -n "$codexsearch_cwd" ]; then
        codexsearch_name=${codexsearch_cwd%/}
        codexsearch_name=${codexsearch_name##*/}
        [ -n "$codexsearch_name" ] || codexsearch_name=/
    fi
    [ -n "$codexsearch_name" ] || codexsearch_name=unknown
    codexsearch_header=$(session_search_header \
        "$codexsearch_name" "$codexsearch_file")
    codexsearch_resume=$(session_search_resume_command \
        "$codexsearch_cwd" codexpr "$codexsearch_id")

    jq -r -L "$(dirname "${BASH_SOURCE[0]}")" --arg query "$codexsearch_query" '
        include "session_search_records";
        def matching_strings:
            [.. | strings |
             select(ascii_downcase | contains($query | ascii_downcase))];
        matching_strings as $matches |
        select($matches | length > 0) |
        session_search_match_output($matches[0])
    ' --join-output "$codexsearch_file" 2>/dev/null |
    session_search_print_matches \
        "$codexsearch_header" "$codexsearch_resume" "$session_search_group" \
        codex "$codexsearch_cwd" "$codexsearch_file"
done
