#!/bin/zsh
set -eux -o pipefail
cd "${0:A:h}/../.."
exec /usr/bin/python3 -B -m macbook.metered.launchd "${1:-install}"
