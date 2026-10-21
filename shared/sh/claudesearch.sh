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

for claudesearch_dependency in grep jq python3; do
    if ! command -v "$claudesearch_dependency" >/dev/null 2>&1; then
        echo "claudesearch: required command not found: $claudesearch_dependency" >&2
        exit 127
    fi
done

claudesearch_query=$session_search_query
claudesearch_root=${CLAUDESEARCH_ROOT:-${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}}
claudesearch_projects="$claudesearch_root/projects"

if [ ! -d "$claudesearch_projects" ]; then
    echo "claudesearch: projects directory not found: $claudesearch_projects" >&2
    exit 1
fi

# awk works in bytes under the C locale, matching jq's ASCII-only case folding.
# The excerpt window is widened so it never splits a multibyte UTF-8 character.
excerpt() {
    LC_ALL=C awk -v query="$claudesearch_query" '
        function is_continuation(byte) {
            return byte >= "\200" && byte < "\300"
        }
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
            while (start_at > 1 && is_continuation(substr(text, start_at, 1))) start_at--
            while (end_at < length(text) && is_continuation(substr(text, end_at + 1, 1))) end_at++
            excerpt_length = end_at - start_at + 1

            result = substr(text, start_at, excerpt_length)
            if (start_at > 1) result = "..." result
            if (start_at + excerpt_length - 1 < length(text)) result = result "..."
            print result
        }
    '
}

session_search_files claude "$claudesearch_root" |
while IFS= read -r -d '' claudesearch_provider && IFS= read -r -d '' claudesearch_file; do
    claudesearch_id=$(basename "$claudesearch_file" .jsonl)

    # Lines are parsed individually so a partially written line in an active
    # session does not hide the rest of the file.
    claudesearch_cwd=""
    claudesearch_name=""
    {
        IFS= read -r claudesearch_cwd
        IFS= read -r claudesearch_name
    } < <(jq -nRr '
        reduce (inputs | fromjson? | objects) as $event ({};
            if $event.type == "custom-title" and ($event.customTitle | type) == "string"
                and $event.customTitle != "" then .custom = $event.customTitle else . end |
            if $event.type == "ai-title" and ($event.aiTitle | type) == "string"
                and $event.aiTitle != "" then .ai = $event.aiTitle else . end |
            if .cwd == null and ($event.cwd | type) == "string"
                then .cwd = $event.cwd else . end) |
        (.cwd // ""), (.custom // .ai // "" | gsub("[[:space:]]+"; " "))
    ' "$claudesearch_file" 2>/dev/null)

    if [ -z "$claudesearch_name" ] && [ -n "$claudesearch_cwd" ]; then
        claudesearch_name=${claudesearch_cwd%/}
        claudesearch_name=${claudesearch_name##*/}
        [ -n "$claudesearch_name" ] || claudesearch_name=/
    fi
    [ -n "$claudesearch_name" ] || claudesearch_name=unknown
    claudesearch_header=$(session_search_header \
        "$claudesearch_name" "$claudesearch_file")
    claudesearch_resume=$(session_search_resume_command \
        "$claudesearch_cwd" claudepr "$claudesearch_id")

    # Search only conversation text: prompts, replies, thinking, tool inputs and
    # results, and queued prompts/notifications. Injected context (CLAUDE.md,
    # skill text, reminders) and per-event metadata such as cwd would otherwise
    # match nearly every session.
    jq -Rr --join-output -L "$(dirname "${BASH_SOURCE[0]}")" --arg query "$claudesearch_query" '
        include "session_search_records";
        def content_strings:
            if type == "string" then .
            elif type == "array" then .[] | content_strings
            elif type == "object" then
                if .type == "text" then .text
                elif .type == "thinking" then .thinking
                elif .type == "tool_use" then .input | .. | strings
                elif .type == "tool_result" then .content | content_strings
                else empty
                end
            else empty
            end;
        def searchable_strings:
            if (.type == "user" and (.isMeta | not)) or .type == "assistant" then
                .message.content | content_strings
            elif .type == "attachment" and .attachment.type == "queued_command" then
                .attachment.prompt | content_strings
            else empty
            end;
        fromjson? | objects |
        [searchable_strings | strings |
         select(ascii_downcase | contains($query | ascii_downcase))] as $matches |
        select($matches | length > 0) |
        session_search_match_output($matches[0])
    ' "$claudesearch_file" 2>/dev/null |
    session_search_print_matches \
        "$claudesearch_header" "$claudesearch_resume" "$session_search_group" \
        claude "$claudesearch_cwd" "$claudesearch_file"
done
