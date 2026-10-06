#!/bin/sh
set -eu

case ${1:-} in
    0|0.*|'') exit 0 ;;
    1) /bin/sleep 0.5 ;;
    *) exit 0 ;;
esac
