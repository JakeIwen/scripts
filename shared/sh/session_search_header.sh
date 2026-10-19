#!/usr/bin/env bash

session_search_usage() {
    local grouping='Show one result per session, with the match count and latest excerpt.'
    if [ "$1" = llmsearch ]; then
        grouping='Group by directory and tool, with total matches and the latest hit.'
    fi
    cat <<EOF
Usage: $1 [flags] [--] <term>

Search session events for a literal, case-insensitive term.

Flags:
  -g, --group  $grouping
  -h, --help   Show this help and exit.
  --          Treat all remaining arguments as search text.
EOF
}

session_search_modified() {
    stat -c %Y -- "$1" 2>/dev/null || stat -f %m -- "$1" 2>/dev/null
}

session_search_parse_args() {
    session_search_group=false
    session_search_help=false
    session_search_query=""
    local literal=false arg
    for arg in "$@"; do
        if [ "$literal" = false ]; then
            case "$arg" in
                -g|--group) session_search_group=true; continue ;;
                -h|--help) session_search_help=true; continue ;;
                --) literal=true; continue ;;
            esac
        fi
        session_search_query="${session_search_query:+$session_search_query }$arg"
    done
    [ "$session_search_help" = true ] || [ -n "$session_search_query" ]
}

# Use modification time: searching a transcript must not reset its activity age.
session_search_header() {
    local name=$1 file=$2
    local modified elapsed count unit age
    modified=$(session_search_modified "$file") || modified=""
    case "$modified" in
        ''|*[!0-9]*) age="unknown age" ;;
        *)
            elapsed=$(( $(date +%s) - modified ))
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
    printf '%s | %s' "$age" "$name" |
        tr '\n\r\t' '   '
    printf '\n'
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
    local text context latest="" count=0
    while IFS= read -r -d '' text; do
        context=$(printf '%s\n' "$text" | excerpt)
        [ -n "$context" ] || continue
        count=$((count + 1))
        if [ "$grouped" = true ]; then
            latest=$context
        else
            printf '%s\n%s\n%s\n\n' "$header" "$resume" "$context"
        fi
    done
    if [ "$grouped" = true ] && [ "$count" -gt 0 ]; then
        printf '%s matches | %s\n%s\n%s\n\n' "$count" "$header" "$resume" "$latest"
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
