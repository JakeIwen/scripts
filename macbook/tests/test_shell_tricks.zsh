#!/bin/zsh
set -eu
if [[ $(uname -s) != Darwin ]]; then
    printf 'SKIP: Mac shell tricks require macOS/Zsh path and BSD stat semantics\n'
    exit 0
fi
SHELL_TRICKS_TEST_ROOT=${0:A:h:h:h}
SHELL_TRICKS_TEST_HOST=Mac
source "$SHELL_TRICKS_TEST_ROOT/shared/tests/shell_tricks_cases.sh"
