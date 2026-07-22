#!/usr/bin/env bash

# Search Codex JSONL sessions and print a readable block for every matching event.

set -u

usage() {
    echo "usage: codexsearch <term>" >&2
}

if [ "$#" -eq 0 ] || [ -z "$*" ]; then
    usage
    exit 2
fi

for codexsearch_dependency in grep jq; do
    if ! command -v "$codexsearch_dependency" >/dev/null 2>&1; then
        echo "codexsearch: required command not found: $codexsearch_dependency" >&2
        exit 127
    fi
done

codexsearch_query=$*
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

# grep identifies matching JSONL files quickly. jq then limits output to the
# individual JSON events and human-readable string values containing the term.
grep -RIl -i -F -- "$codexsearch_query" "$codexsearch_sessions" 2>/dev/null |
while IFS= read -r codexsearch_file; do
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

    jq -r --arg query "$codexsearch_query" '
        def matching_strings:
            [.. | strings |
             select(ascii_downcase | contains($query | ascii_downcase))];
        matching_strings as $matches |
        select($matches | length > 0) |
        $matches[0], "\u0000"
    ' --join-output "$codexsearch_file" 2>/dev/null |
    while IFS= read -r -d '' codexsearch_text; do
        codexsearch_context=$(printf '%s\n' "$codexsearch_text" | excerpt)
        [ -n "$codexsearch_context" ] || continue
        printf '%s\n%s\n%s\n\n' \
            "$codexsearch_name" "$codexsearch_id" "$codexsearch_context"
    done
done
