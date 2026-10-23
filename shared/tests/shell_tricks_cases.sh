# Behavior tests, sourced by the Bash and Zsh test entry points.
set -eu

assert_equal() {
    [ "$1" = "$2" ] || { printf 'expected <%s>, got <%s>\n' "$1" "$2" >&2; exit 1; }
}
assert_contains() {
    case "$1" in *"$2"*) ;; *) printf 'missing <%s> in <%s>\n' "$2" "$1" >&2; exit 1 ;; esac
}
assert_fails() {
    if "$@" >"$work/error" 2>&1; then
        printf 'unexpected success: %s\n' "$*" >&2; exit 1
    fi
}

work=$(mktemp -d "${TMPDIR:-/tmp}/shell-tricks-test.XXXXXXXX")
work=$(cd "$work" && pwd -P)
trap 'rm -rf -- "$work"' EXIT
export HOME="$work/home" TMPDIR="$work" TEST_ARGS="$work/args" TEST_CLIP="$work/clipboard"
export TEST_MOUNT="$work/mount" TEST_NOTIFY="$work/notify"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
mkdir -p "$HOME/dev/scripts/shared/sh" "$HOME/scripts" "$work/bin"
for filename in shell_tricks_common.sh shell_tricks.md; do
    ln -s "$SHELL_TRICKS_TEST_ROOT/shared/sh/$filename" "$HOME/dev/scripts/shared/sh/$filename"
    ln -s "$SHELL_TRICKS_TEST_ROOT/shared/sh/$filename" "$HOME/scripts/$filename"
done

# Host operations record argv/stdin instead of contacting desktop, firmware,
# services or the network. Git/filesystem behavior below uses real temp repos.
cat > "$work/bin/stub" <<'STUB'
#!/bin/sh
name=${0##*/}
printf '%s\n' "$name" "$@" > "$TEST_ARGS"
case "$name" in
    pbcopy) cat > "$TEST_CLIP" ;;
    mount) cat "$TEST_MOUNT" ;;
    vcgencmd) printf '%s\n' "firmware:$1" ;;
    python3) printf '%s\n' "$@" > "$TEST_NOTIFY"; exit "${TEST_NOTIFY_RC:-0}" ;;
    less) printf 'pager should not run on redirected output\n' >&2; exit 90 ;;
esac
STUB
chmod +x "$work/bin/stub"
for name in pbcopy mount vcgencmd python3 open journalctl systemctl less; do
    ln -s stub "$work/bin/$name"
done
export PATH="$work/bin:$PATH"

before_cwd=$PWD
before_flags=$-
if [ "$SHELL_TRICKS_TEST_HOST" = Mac ]; then
    source "$SHELL_TRICKS_TEST_ROOT/macbook/scripts/shell_tricks.zsh" > "$work/load" 2>&1
else
    source "$SHELL_TRICKS_TEST_ROOT/pi/scripts/shell_tricks.bash" > "$work/load" 2>&1
fi
[ ! -s "$work/load" ]
[ ! -e "$TEST_ARGS" ]
assert_equal "$before_cwd" "$PWD"
assert_equal "$before_flags" "$-"
tricks > "$work/all"
cmp "$_SHELL_TRICKS_DOC" "$work/all"
assert_contains "$(tricks -l)" 'gpickaxe'
for name in $(tricks -l | awk '{print $1}'); do
    assert_contains "$(tricks "$name")" "## $name"
done
one=$(tricks croot)
assert_contains "$one" '## croot'
case "$one" in *'## cproj'*) exit 1 ;; esac
assert_fails tricks does-not-exist
assert_contains "$(cat "$work/error")" 'Valid names:'
# Literal lookup must not interpret awk escapes or regex metacharacters.
assert_fails tricks '\143root'
assert_fails tricks 'c.*'
assert_fails tricks croot extra

mkdir -p "$HOME/dev/project space" "$HOME/Documents/agent-shared"
cproj 'project space'
assert_equal "$HOME/dev/project space" "$PWD"
assert_fails cproj ../scripts
assert_equal "$HOME/dev/project space" "$PWD"
assert_fails cproj missing
cproj
assert_equal "$HOME/dev" "$PWD"
ctmp > "$work/scratchpath"
assert_equal "$PWD" "$(cat "$work/scratchpath")"
case "$PWD" in "$work"/shell-tricks.*) ;; *) exit 1 ;; esac
[ -d "$PWD" ]
if [ "$SHELL_TRICKS_TEST_HOST" = Mac ]; then
    assert_equal 700 "$(stat -f %Lp "$PWD")"
else
    # GNU stat on the Pi, BSD stat when testing Bash on the Mac.
    mode=$(stat -c %a "$PWD" 2>/dev/null || stat -f %Lp "$PWD")
    assert_equal 700 "$mode"
fi
assert_fails croot
assert_fails ctmp argument

mkdir -p "$work/repository/sub dir"
git -c init.defaultBranch=main init -q "$work/repository"
git -C "$work/repository" config user.name 'Shell test'
git -C "$work/repository" config user.email 'shell-test@example.invalid'
cd "$work/repository"
printf 'needle\n' > tracked.txt
printf 'build/\n' > .gitignore
git add tracked.txt .gitignore
git commit -qm 'introduce needle'
printf 'replacement\n' > tracked.txt
git commit -qam 'remove needle'
printf 'staged\n' >> tracked.txt
git add tracked.txt
printf 'unstaged\n' >> tracked.txt
printf 'new\n' > untracked.txt
mkdir build
printf 'ignored\n' > build/output.log
cd 'sub dir'
croot
assert_equal "$work/repository" "$PWD"
summary=$(gsummary)
assert_contains "$summary" 'MM tracked.txt'
assert_contains "$summary" '?? untracked.txt'
assert_contains "$summary" 'Unstaged change sizes:'
assert_contains "$summary" 'Staged change sizes:'
assert_contains "$(gworktrees)" "$work/repository"
assert_contains "$(gworktrees)" '[main]'
git worktree add -qb fixture "$work/linked tree"
mkdir "$work/linked tree/nested"
cd "$work/linked tree/nested"
croot
assert_equal "$work/linked tree" "$PWD"
assert_contains "$(gworktrees)" "$work/repository"
assert_contains "$(gworktrees)" "$work/linked tree"
cd "$work/repository"
found=$(gpickaxe needle tracked.txt)
assert_contains "$found" 'introduce needle'
assert_contains "$found" 'remove needle'
assert_equal '' "$(gpickaxe needle missing.txt)"
assert_fails gpickaxe
assert_fails gpickaxe ''
assert_contains "$(gignored build/output.log)" '.gitignore:1:build/'
assert_fails gignored tracked.txt
assert_fails gignored
assert_fails gsummary argument
assert_fails gworktrees argument

# PATH values include duplicate/empty entries and a literal backslash.
old_path=$PATH
PATH="$PATH::/directory with spaces:/back\\slash:"
paths=$(pathlines)
assert_contains "$paths" '. (current directory)'
assert_contains "$paths" '/directory with spaces'
assert_contains "$paths" '/back\slash'
PATH=''
assert_equal ' 1  . (current directory)' "$(pathlines)"
PATH=$old_path
assert_fails pathlines argument

# Argument quoting, exit codes and parent cwd survive notification wrappers.
aftercmd printf '%s\n' 'two words' '$(must stay literal)' > "$work/result"
assert_equal "$(printf '%s\n' 'two words' '$(must stay literal)')" "$(cat "$work/result")"
assert_contains "$(cat "$TEST_NOTIFY")" 'Finished (exit 0).'
case "$(cat "$TEST_NOTIFY")" in *'must stay literal'*) exit 1 ;; esac
code=0
aftercmd sh -c 'exit 7' || code=$?
assert_equal 7 "$code"
assert_contains "$(cat "$TEST_NOTIFY")" 'Finished (exit 7).'
aftercmd cd "$HOME"
assert_equal "$work/repository" "$PWD"
TEST_NOTIFY_RC=1 aftercmd true 2> "$work/warning"
assert_contains "$(cat "$work/warning")" 'notification failed'
assert_fails aftercmd

if [ "$SHELL_TRICKS_TEST_HOST" = Mac ]; then
    _SHELL_TRICKS_MOUNT="$work/fake share"
    _SHELL_TRICKS_SHARE="$_SHELL_TRICKS_MOUNT/Documents/agent-shared"
    mkdir -p "$_SHELL_TRICKS_SHARE"
    : > "$TEST_MOUNT"
    assert_fails cshare
    assert_equal "$work/repository" "$PWD"
    printf 'server on %s (smbfs, nodev)\n' "$_SHELL_TRICKS_MOUNT" > "$TEST_MOUNT"
    cshare
    assert_equal "$_SHELL_TRICKS_SHARE" "$PWD"
    printf 'quotes " and \\ and\nnewlines\n\n' > "$work/text file"
    clipfile "$work/text file"
    cmp "$work/text file" "$TEST_CLIP"
    assert_fails clipfile "$work/no-file"
    cmp "$work/text file" "$TEST_CLIP"
    ln -s "$work/text file" "$work/link"
    copypath "$work/link"
    assert_equal "$work/text file" "$(cat "$TEST_CLIP")"
    copypath
    assert_equal "$PWD" "$(cat "$TEST_CLIP")"
    reveal "$work/link"
    assert_equal "$(printf 'open\n-R\n%s' "$work/text file")" "$(cat "$TEST_ARGS")"
    assert_fails reveal "$work/missing"
else
    cshare
    assert_equal "$HOME/Documents/agent-shared" "$PWD"
    junit example.service
    assert_equal "$(printf 'journalctl\n--boot\n--unit=example.service\n--lines=100\n--no-pager')" "$(cat "$TEST_ARGS")"
    junit 'unit with spaces.service' 25
    assert_contains "$(cat "$TEST_ARGS")" '--unit=unit with spaces.service'
    assert_contains "$(cat "$TEST_ARGS")" '--lines=25'
    junit example.service follow
    assert_contains "$(cat "$TEST_ARGS")" '--follow'
    for invalid in 0 10001 -2 nonsense '1;echo bad'; do
        assert_fails junit example.service "$invalid"
    done
    assert_fails junit
    failedunits
    assert_equal "$(printf 'systemctl\n--failed\n--no-pager\n--full')" "$(cat "$TEST_ARGS")"
    failedunits --user
    assert_contains "$(cat "$TEST_ARGS")" '--user'
    assert_fails failedunits restart
    assert_equal "$(printf 'firmware:measure_temp\nfirmware:get_throttled')" "$(pitherm)"
    assert_fails pitherm argument
fi
assert_fails cshare argument
printf 'PASS: shell tricks (%s, %s)\n' "$SHELL_TRICKS_TEST_HOST" "${ZSH_VERSION:-${BASH_VERSION:-unknown}}"
