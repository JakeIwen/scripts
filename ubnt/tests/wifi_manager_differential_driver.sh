#!/bin/sh
set -eu

old_entrypoint=$1
new_scripts=$2
seed_dir=$3
runtime_dir=$4
capture_dir=$5
stdin_file=$6
shift 6

entrypoint="$runtime_dir/persistent/scripts/wifi_manager.sh"

reset_runtime() {
    rm -rf "$runtime_dir"
    mkdir -p "$runtime_dir/persistent/scripts"
    cp -R "$seed_dir"/. "$runtime_dir"/
    for support_file in "$new_scripts"/parse-iwlist.awk \
        "$new_scripts"/ensure_ssh_keys.sh; do
        [ -f "$support_file" ] || continue
        cp "$support_file" "$runtime_dir/persistent/scripts/"
    done
    for module_file in "$new_scripts"/wifi_manager_*.sh; do
        [ -f "$module_file" ] || continue
        cp "$module_file" "$runtime_dir/persistent/scripts/"
    done
    if [ "${DIFF_ACTIVE_LOCK:-0}" = 1 ]; then
        mkdir -p "$runtime_dir/state/lock"
        printf '%s\n' "$$" > "$runtime_dir/state/lock/pid"
    fi
}

capture_runtime() {
    variant=$1
    variant_dir="$capture_dir/$variant"
    mkdir -p "$variant_dir/tree"
    for capture_source in "$runtime_dir"/* "$runtime_dir"/.[!.]* "$runtime_dir"/..?*; do
        capture_name=${capture_source##*/}
        # Compare every mutable path, excluding the staged code and mock tools.
        case $capture_name in bin|persistent) continue ;; esac
        if [ -e "$capture_source" ] || [ -L "$capture_source" ]; then
            cp -pR "$capture_source" "$variant_dir/tree/$capture_name"
        fi
    done
}

run_variant() {
    variant=$1
    source_entrypoint=$2
    shift 2
    variant_dir="$capture_dir/$variant"
    reset_runtime
    cp "$source_entrypoint" "$entrypoint"
    chmod 755 "$entrypoint"
    mkdir -p "$variant_dir"
    # Run both complete entrypoints with the same $0 and $$, without rewriting
    # their bodies or normalizing PID-bearing output, filenames, or state.
    if (
        set +eu
        . "$entrypoint" "$@"
    ) < "$stdin_file" > "$variant_dir/stdout" 2> "$variant_dir/stderr"; then
        variant_status=0
    else
        variant_status=$?
    fi
    printf '%s\n' "$variant_status" > "$variant_dir/status"
    capture_runtime "$variant"
}

rm -rf "$capture_dir"
mkdir -p "$capture_dir"
run_variant old "$old_entrypoint" "$@"
run_variant new "$new_scripts/wifi_manager.sh" "$@"
