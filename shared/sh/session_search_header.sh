#!/usr/bin/env bash

session_search_usage() {
    cat <<EOF
Usage: $1 [flags] [--] <term>

Search session events for a literal, case-insensitive term.
Sessions and their matching excerpts are shown newest first.

Flags:
  -t, --title  Search saved session titles only (literal, case-insensitive).
  -g, --group  Show one result per session, with the match count and latest excerpt.
  -h, --help   Show this help and exit.
  --          Treat all remaining arguments as search text.
EOF
}

session_search_modified() {
    stat -c %Y -- "$1" 2>/dev/null || stat -f %m -- "$1" 2>/dev/null
}

# List by activity before reading transcript contents, so recent hits can be
# displayed while older sessions are still being searched. Arguments are
# provider/root pairs; NULs preserve paths containing whitespace or newlines.
session_search_files() {
    local provider file
    # Only llmsearch's explicitly tagged child dispatch selects a single file.
    if [ "$#" -eq 2 ] && [ "${SESSION_SEARCH_FILE_PROVIDER:-}" = "$1" ] &&
        [ -n "${SESSION_SEARCH_FILE:-}" ]; then
        if [ -f "$SESSION_SEARCH_FILE" ] && [ ! -L "$SESSION_SEARCH_FILE" ]; then
            printf '%s\0%s\0' "$1" "$SESSION_SEARCH_FILE"
        fi
        return 0
    fi
    python3 "$(dirname "${BASH_SOURCE[0]}")/session_search_paths.py" "$@" |
    while IFS= read -r -d '' provider && IFS= read -r -d '' file; do
        if [ -f "$file" ] && [ ! -L "$file" ] &&
            { [ "$session_search_title" = true ] ||
              grep -qiF -- "$session_search_query" "$file" 2>/dev/null; }; then
            printf '%s\0%s\0' "$provider" "$file"
        fi
    done
}

# A caller that offers --grep sets session_search_grep=false before parsing;
# for the others it is search text. The query is also kept split at --, into
# session_search_words before it and session_search_literal after it.
session_search_parse_args() {
    session_search_group=false
    session_search_title=false
    session_search_help=false
    session_search_query=""
    session_search_words=""
    session_search_literal=""
    local literal=false arg
    for arg in "$@"; do
        if [ "$literal" = false ]; then
            case "$arg" in
                -t|--title) session_search_title=true; continue ;;
                -g|--group) session_search_group=true; continue ;;
                -h|--help) session_search_help=true; continue ;;
                --grep)
                    if [ -n "${session_search_grep+set}" ]; then
                        session_search_grep=true
                        continue
                    fi
                    ;;
                --) literal=true; continue ;;
            esac
        fi
        session_search_query="${session_search_query:+$session_search_query }$arg"
        if [ "$literal" = true ]; then
            session_search_literal="${session_search_literal:+$session_search_literal }$arg"
        else
            session_search_words="${session_search_words:+$session_search_words }$arg"
        fi
    done
    [ "$session_search_help" = true ] || [ -n "$session_search_query" ]
}

# Use modification time: searching a transcript must not reset its activity age.
session_search_header() {
    local modified
    modified=$(session_search_modified "$2") || modified=""
    session_search_header_at "$1" "$modified"
}

# Takes the activity time in epoch seconds. Under bash 5 it forks nothing, so
# llmsearch can label hundreds of index results quickly on the Pi.
session_search_header_at() {
    local name=$1 modified=$2
    local elapsed count unit age line
    case "$modified" in
        ''|*[!0-9]*) age="unknown age" ;;
        *)
            elapsed=$(( ${EPOCHSECONDS:-$(date +%s)} - modified ))
            [ "$elapsed" -ge 0 ] || elapsed=0
            if [ "$elapsed" -lt 86400 ]; then
                count=$((elapsed / 3600))
                unit=hour
            else
                count=$((elapsed / 86400))
                unit=day
            fi
            [ "$count" -eq 1 ] || unit="${unit}s"
            age="$count $unit ago"
            ;;
    esac
    line="$age | $name"
    printf '%s\n' "${line//[$'\n\r\t']/ }"
}

session_search_quote_word() {
    local word=$1 escaped_quote="'\\''"
    case "$word" in
        ''|*[!a-zA-Z0-9_./-]*) printf "'%s'" "${word//\'/$escaped_quote}" ;;
        *) printf '%s' "$word" ;;
    esac
}

session_search_resume_command() {
    local cwd=$1 command=$2 id=$3
    if [ -n "$cwd" ]; then
        case "$cwd" in
            "$HOME") printf 'cd ~' ;;
            "$HOME"/*)
                # Keep the tilde outside quotes so the shell expands HOME.
                printf 'cd ~/'
                session_search_quote_word "${cwd#"$HOME"/}"
                ;;
            *)
                case "$cwd" in -*) printf 'cd -- ' ;; *) printf 'cd ' ;; esac
                session_search_quote_word "$cwd"
                ;;
        esac
        printf ' && '
    fi
    printf '%s ' "$command"
    session_search_quote_word "$id"
    printf '\n'
}

# Each NUL-delimited input is one matching event, in transcript append order.
session_search_print_matches() {
    local header=$1 resume=$2 grouped=$3
    if [ "${SESSION_SEARCH_RECORDS:-}" = 1 ]; then
        session_search_collect_record "$header" "$resume" "$4" "$5" "$6"
        return
    fi
    local text context latest="" count=0 index
    local contexts=()
    while IFS= read -r -d '' text; do
        context=$(printf '%s\n' "$text" | excerpt)
        [ -n "$context" ] || continue
        count=$((count + 1))
        if [ "$grouped" = true ]; then
            latest=$context
        else
            contexts[${#contexts[@]}]=$context
        fi
    done
    if [ "$grouped" = true ] && [ "$count" -gt 0 ]; then
        printf '%s matches | %s\n%s\n%s\n\n' "$count" "$header" "$resume" "$latest"
    else
        for ((index=${#contexts[@]} - 1; index >= 0; index--)); do
            printf '%s\n%s\n%s\n\n' "$header" "$resume" "${contexts[index]}"
        done
    fi
}

session_search_collect_record() {
    local header=$1 resume=$2 provider=$3 cwd=$4 file=$5
    local matches context modified
    matches=$(jq -cn 'reduce inputs as $match ({count: 0};
        .count += 1 | .text = $match.text | .timestamp = $match.timestamp)') || return
    [ "$(printf '%s' "$matches" | jq -r .count)" -gt 0 ] || return 0
    context=$(printf '%s' "$matches" | jq -r .text | excerpt)
    [ -n "$context" ] || return 0
    modified=$(session_search_modified "$file") || modified=0
    printf '%s' "$matches" | jq -c -L "$(dirname "${BASH_SOURCE[0]}")" \
        --arg provider "$provider" --arg cwd "$cwd" --argjson modified "$modified" \
        --arg header "$header" --arg resume "$resume" --arg context "$context" '
        include "session_search_records";
        session_search_record($provider; $cwd; $modified; $header; $resume; $context)
    '
}

# Feed one saved title through the same display/records contract as event hits.
# Callers pass the real title before their directory-name fallback is applied.
session_search_title_match() {
    jq -nj -L "$(dirname "${BASH_SOURCE[0]}")" \
        --arg title "$1" --arg query "$session_search_query" '
        include "session_search_records";
        select($title != "" and ($title | ascii_downcase | contains($query | ascii_downcase))) |
        session_search_match_output($title)
    '
}
