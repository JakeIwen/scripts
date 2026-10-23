#!/bin/bash
set -eu
SHELL_TRICKS_TEST_ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
SHELL_TRICKS_TEST_HOST=Pi
source "$SHELL_TRICKS_TEST_ROOT/shared/tests/shell_tricks_cases.sh"
