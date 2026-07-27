#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
deployment="$script_dir/../scp_to_device.sh"

line_number() {
    grep -nF "$1" "$deployment" | sed -n '1s/:.*//p'
}

stage_boundary=$(line_number 'if [[ "$mode" == --stage-only ]]')
profile_copy=$(line_number 'cp -p \"\$persistent/config/.profile\" \"\$persistent/.profile.new\"')
profile_move=$(line_number 'mv \"\$persistent/.profile.new\" \"\$persistent/.profile\"')
activation_boundary=$(line_number "if [ '\$activate' = yes ]")

for required_line in \
    "$stage_boundary" "$profile_copy" "$profile_move" "$activation_boundary"; do
    case $required_line in
        ''|*[!0-9]*)
            echo 'Deployment profile-copy contract is missing.' >&2
            exit 1
            ;;
    esac
done

[ "$stage_boundary" -lt "$profile_copy" ]
[ "$profile_copy" -lt "$profile_move" ]
[ "$profile_move" -lt "$activation_boundary" ]

printf 'deployment: ok\n'
