#!/usr/bin/env bash

# Search Claude Code JSONL sessions and print a readable block for every
# matching conversation event.

set -u

usage() {
    echo "usage: claudesearch <term>" >&2
}

if [ "$#" -eq 0 ] || [ -z "$*" ]; then
    usage
    exit 2
fi

for claudesearch_dependency in grep jq; do
    if ! command -v "$claudesearch_dependency" >/dev/null 2>&1; then
        echo "claudesearch: required command not found: $claudesearch_dependency" >&2
        exit 127
    fi
done

claudesearch_query=$*
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

# Sessions are projects/<project>/<session-id>.jsonl. Subagent transcripts live
# deeper under projects/<project>/<session-id>/ and are skipped; their prompts
# and final reports already appear in the parent session. grep finds candidate
# files quickly and ls orders them oldest to newest by last activity.
find "$claudesearch_projects" -mindepth 2 -maxdepth 2 -type f -name '*.jsonl' \
    -exec grep -Il -i -F --null -- "$claudesearch_query" {} + 2>/dev/null |
xargs -0 -r ls -1tr -- 2>/dev/null |
while IFS= read -r claudesearch_file; do
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

    # Search only conversation text: prompts, replies, thinking, tool inputs and
    # results, and queued prompts/notifications. Injected context (CLAUDE.md,
    # skill text, reminders) and per-event metadata such as cwd would otherwise
    # match nearly every session.
    jq -Rr --join-output --arg query "$claudesearch_query" '
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
        $matches[0], "\u0000"
    ' "$claudesearch_file" 2>/dev/null |
    while IFS= read -r -d '' claudesearch_text; do
        claudesearch_context=$(printf '%s\n' "$claudesearch_text" | excerpt)
        [ -n "$claudesearch_context" ] || continue
        printf '%s\n%s\n%s\n\n' \
            "$claudesearch_name" "$claudesearch_id" "$claudesearch_context"
    done
done
