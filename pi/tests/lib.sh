# Sourced by the bash shell tests after they set `set -u`.
# Defines only the shared fail and assert_eq helpers.
# Tests keep their own set -u, repo_root, temp dir, and trap so each
# file's error mode stays visible at its top.

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_eq() {
  local expected=$1 actual=$2 description=$3
  [[ "$actual" == "$expected" ]] ||
    fail "$description (expected '$expected', got '$actual')"
}
