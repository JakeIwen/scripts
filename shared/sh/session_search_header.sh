#!/usr/bin/env bash

session_search_usage() {
    cat <<EOF
Usage: $1 [flags] [--] <term>

Search session events for a literal, case-insensitive term.
Sessions and their matching excerpts are shown newest first.

Flags:
  -t, --title  Search saved session titles only (literal, case-insensitive).
  -g, --group  Show one result per session, with the match count and latest excerpt.
  -N          Show N message lines before and after each match (e.g. -2).
  -a N, -aN   Show N lines after; later context flags override each side.
  -b N, -bN   Show N lines before (non-negative integers).
  -h, --help   Show this help and exit.
  --          Treat all remaining arguments as search text.
EOF
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
    session_search_before=0
    session_search_after=0
    session_search_context_requested=false
    local literal=false arg side value
    while [ "$#" -gt 0 ]; do
        arg=$1
        shift
        if [ "$literal" = false ]; then
            case "$arg" in
                -a|-b|-a[0-9]*|-b[0-9]*|-[0-9]*)
                    case "$arg" in
                        -a|-b)
                            side=$arg
                            [ "$#" -gt 0 ] || return 2
                            value=$1
                            shift
                            ;;
                        -a*|-b*) side=${arg:0:2}; value=${arg:2} ;;
                        *) side=both; value=${arg:1} ;;
                    esac
                    # Non-numeric -words remain index syntax/search text.
                    case "$value" in
                        ''|*[!0-9]*) [ "$side" = both ] || return 2 ;;
                        *)
                            session_search_context_requested=true
                            [ "$side" = -a ] || session_search_before=$value
                            [ "$side" = -b ] || session_search_after=$value
                            continue
                            ;;
                    esac
                    ;;
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
    line="${3:-}$age | $name"
    session_search_color_line "${line//[$'\n\r\t']/ }"
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

# Decide at the output boundary, before pipelines make stdout ambiguous.
session_search_color_start=""
session_search_color_end=""
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${SESSION_SEARCH_RECORDS:-}" != 1 ]; then
    session_search_color_start=$'\033[38;5;215m'
    session_search_color_end=$'\033[0m'
fi

session_search_color_line() {
    printf '%s%s%s\n' "$session_search_color_start" "$1" "$session_search_color_end"
}

# One extractor serves the complete ordered stream, not one process per file.
session_search_extract() {
    local flags=()
    [ "$session_search_group" = false ] || flags+=(--group)
    [ "$session_search_title" = false ] || flags+=(--title)
    [ "$session_search_context_requested" = false ] || flags+=(--context)
    [ "${SESSION_SEARCH_RECORDS:-}" != 1 ] || flags+=(--records)
    python3 "$(dirname "${BASH_SOURCE[0]}")/session_search_extract.py" \
        ${flags[@]+"${flags[@]}"} --before "$session_search_before" --after "$session_search_after" \
        --codex-root "${CODEXSEARCH_ROOT:-$HOME/.codex}" -- "$session_search_query"
}
