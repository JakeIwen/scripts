#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
ubnt_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
transport_helper="$script_dir/fake_deployment_transport.py"
remote_command_fixture="$script_dir/fixtures/fake_remote_command.sh"
profile_fixture="$script_dir/fixtures/deployment-profile.cfg"
python_bin=$(command -v python3)
suite_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-deployment-test.XXXXXX")
case_count=0
current_case=setup
trap 'rm -rf "$suite_root"' EXIT HUP INT TERM

fail() {
    printf 'deployment case %s failed: %s\n' "$current_case" "$*" >&2
    if [ -n "${DEPLOY_OUTPUT:-}" ] && [ -f "$DEPLOY_OUTPUT" ]; then
        printf '%s\n' '--- deploy output ---' >&2
        /bin/cat "$DEPLOY_OUTPUT" >&2
    fi
    exit 1
}

assert_contains() {
    grep -F "$2" "$1" >/dev/null || fail "expected '$2' in $1"
}

assert_not_contains() {
    if grep -F "$2" "$1" >/dev/null; then
        fail "did not expect '$2' in $1"
    fi
}

assert_file_equal() {
    cmp -s "$1" "$2" || fail "files differ: $1 and $2"
}

tree_digest() {
    "$python_bin" "$transport_helper" tree-digest "$1"
}

sync_fake_flash() {
    /bin/rm -rf "$DEVICE_ROOT/flash/persistent"
    /bin/cp -pR "$DEVICE_ROOT/etc/persistent" "$DEVICE_ROOT/flash/persistent"
    /bin/cp -p "$DEVICE_ROOT/tmp/system.cfg" "$DEVICE_ROOT/flash/system.cfg"
}

setup_case() {
    label=$1
    CASE_ROOT=$(mktemp -d "$suite_root/$label.XXXXXX")
    WORK_UBNT="$CASE_ROOT/ubnt"
    DEVICE_ROOT="$CASE_ROOT/device"
    TRANSPORT_BIN="$CASE_ROOT/transport-bin"
    REMOTE_BIN="$CASE_ROOT/remote-bin"
    EVENT_LOG="$CASE_ROOT/events.log"
    DEPLOY_OUTPUT="$CASE_ROOT/deploy.out"
    LOCAL_TMP="$CASE_ROOT/local-tmp"

    mkdir -p "$WORK_UBNT" "$DEVICE_ROOT/etc/persistent/config" \
        "$DEVICE_ROOT/etc/persistent/scripts" "$DEVICE_ROOT/etc/persistent/profiles" \
        "$DEVICE_ROOT/etc/persistent/rollback" "$DEVICE_ROOT/etc/dropbear" \
        "$DEVICE_ROOT/tmp" "$DEVICE_ROOT/var/log" "$DEVICE_ROOT/proc/sys/kernel" \
        "$DEVICE_ROOT/dev" "$DEVICE_ROOT/flash" "$DEVICE_ROOT/state" \
        "$TRANSPORT_BIN" "$REMOTE_BIN" "$LOCAL_TMP"
    /bin/cp -R "$ubnt_root/." "$WORK_UBNT/"
    /bin/rm -rf "$WORK_UBNT/private-backups" "$WORK_UBNT/persistent/profiles"

    for command in ssh scp date mktemp; do
        ln -s "$transport_helper" "$TRANSPORT_BIN/$command"
    done
    for command in sh pkill pgrep crond cfgmtd md5sum cp mv rm tar gzip; do
        ln -s "$remote_command_fixture" "$REMOTE_BIN/$command"
    done

    printf '%s\n' '#!/bin/sh' 'crond' > "$DEVICE_ROOT/etc/persistent/rc.postsysinit"
    printf '%s\n' '# old login profile' 'export OLD_DEPLOYMENT=1' > "$DEVICE_ROOT/etc/persistent/profile"
    printf '%s\n' '* * * * * old-command' > "$DEVICE_ROOT/etc/persistent/config/cron"
    printf '%s\n' '# old shell config' > "$DEVICE_ROOT/etc/persistent/config/.profile"
    printf '%s\n' 'ssh-rsa AAAAOLD old@test.invalid' > "$DEVICE_ROOT/etc/persistent/config/raspi_rsa_id.pub"
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$DEVICE_ROOT/etc/persistent/scripts/old.sh"
    printf '%s\n' '{ print }' > "$DEVICE_ROOT/etc/persistent/scripts/old.awk"
    /bin/cp -p "$profile_fixture" "$DEVICE_ROOT/etc/persistent/profiles/camp.cfg"
    printf '%s\n' 'ssh-rsa AAAAEXISTING existing@test.invalid' > "$DEVICE_ROOT/etc/dropbear/authorized_keys"
    printf '%s\n' 'users.1.name=ubnt' 'users.1.password=fake-test-hash' > "$DEVICE_ROOT/tmp/system.cfg"
    ln -s /dev/null "$DEVICE_ROOT/dev/null"
    chmod 750 "$DEVICE_ROOT/etc/persistent/rc.postsysinit" "$DEVICE_ROOT/etc/persistent/scripts/old.sh"
    sync_fake_flash
    : > "$DEVICE_ROOT/state/crond.running"
    : > "$EVENT_LOG"

    export FAKE_CASE_ROOT="$CASE_ROOT"
    export FAKE_DEVICE_ROOT="$DEVICE_ROOT"
    export FAKE_EVENT_LOG="$EVENT_LOG"
    export FAKE_REMOTE_BIN="$REMOTE_BIN"
    export FAKE_TRANSPORT_HELPER="$transport_helper"
    export FAKE_PYTHON="$python_bin"
    unset FAKE_SCP_FAIL_MATCH FAKE_TAR_FAIL FAKE_CFGMTD_WRITE_FAIL \
        FAKE_CFGMTD_READ_FAIL FAKE_CFGMTD_READBACK_CORRUPT \
        FAKE_CFGMTD_READBACK_MISSING FAKE_CFGMTD_READBACK_SYMLINK \
        FAKE_CP_FAIL_MATCH FAKE_MV_FAIL_MATCH \
        FAKE_CROND_FAIL_ONCE FAKE_MUTATE_AFTER_PREPARE FAKE_HOOK_FAIL \
        FAKE_CFGMTD_LAYOUT FAKE_SCP_CORRUPT || true
}

run_deploy() {
    mode=$1
    shift
    set +e
    PATH="$TRANSPORT_BIN:$PATH" TMPDIR="$LOCAL_TMP" \
        /bin/bash "$WORK_UBNT/scp_to_device.sh" "$mode" fake@device "$@" \
        > "$DEPLOY_OUTPUT" 2>&1
    DEPLOY_STATUS=$?
    set -e
}

expect_success() {
    run_deploy "$@"
    [ "$DEPLOY_STATUS" -eq 0 ] || fail "deployment exited $DEPLOY_STATUS"
}

expect_failure() {
    run_deploy "$@"
    [ "$DEPLOY_STATUS" -ne 0 ] || fail 'deployment unexpectedly succeeded'
}

assert_no_live_stop() {
    assert_not_contains "$EVENT_LOG" "pkill"
    [ -e "$DEVICE_ROOT/state/crond.running" ] || fail 'cron was not left running'
}

assert_cron_restarted() {
    grep '^pkill[[:space:]]' "$EVENT_LOG" >/dev/null || fail 'cron was not stopped before failure'
    grep '^crond[[:space:]]' "$EVENT_LOG" >/dev/null || fail 'cron restart was not attempted'
    [ -e "$DEVICE_ROOT/state/crond.running" ] || fail 'cron was not restarted'
}

line_number() {
    grep -n "$2" "$1" | sed -n '1s/:.*//p'
}

assert_order() {
    first=$(line_number "$EVENT_LOG" "$1")
    second=$(line_number "$EVENT_LOG" "$2")
    [ -n "$first" ] && [ -n "$second" ] && [ "$first" -lt "$second" ] || \
        fail "event order missing or reversed: $1 before $2"
}

make_rollback() {
    name=$1
    root="$DEVICE_ROOT/etc/persistent/rollback/$name"
    mkdir -p "$root/config" "$root/scripts"
    printf '%s\n' '#!/bin/sh' 'crond' > "$root/rc.postsysinit"
    printf '%s\n' '# rollback profile' > "$root/profile"
    printf '%s\n' '* * * * * rollback-command' > "$root/config/cron"
    printf '%s\n' '# rollback config' > "$root/config/.profile"
    printf '%s\n' '#!/bin/sh' 'exit 0' > "$root/scripts/rollback.sh"
    printf '%s\n' '{ print }' > "$root/scripts/rollback.awk"
}

verify_installed_tree() {
    assert_file_equal "$WORK_UBNT/persistent/rc.postsysinit" "$DEVICE_ROOT/etc/persistent/rc.postsysinit"
    assert_file_equal "$WORK_UBNT/persistent/profile" "$DEVICE_ROOT/etc/persistent/profile"
    for source in "$WORK_UBNT"/persistent/config/*; do
        assert_file_equal "$source" "$DEVICE_ROOT/etc/persistent/config/${source##*/}"
    done
    assert_file_equal "$WORK_UBNT/persistent/config/.profile" "$DEVICE_ROOT/etc/persistent/config/.profile"
    for source in "$WORK_UBNT"/persistent/scripts/*; do
        assert_file_equal "$source" "$DEVICE_ROOT/etc/persistent/scripts/${source##*/}"
        [ "$(stat -f '%Lp' "$DEVICE_ROOT/etc/persistent/scripts/${source##*/}")" = 750 ] || \
            fail "script mode is not 750: ${source##*/}"
    done
    [ "$(stat -f '%Lp' "$DEVICE_ROOT/etc/persistent/rc.postsysinit")" = 750 ] || \
        fail 'rc.postsysinit mode is not 750'
}

case_stage_only() {
    live_before=$(tree_digest "$DEVICE_ROOT/etc")
    flash_before=$(tree_digest "$DEVICE_ROOT/flash")
    system_before=$(tree_digest "$DEVICE_ROOT/tmp/system.cfg")
    expect_success --stage-only
    [ "$live_before" = "$(tree_digest "$DEVICE_ROOT/etc")" ] || fail 'stage-only changed live files'
    [ "$flash_before" = "$(tree_digest "$DEVICE_ROOT/flash")" ] || fail 'stage-only changed fake flash'
    [ "$system_before" = "$(tree_digest "$DEVICE_ROOT/tmp/system.cfg")" ] || fail 'stage-only changed system.cfg'
    assert_file_equal "$profile_fixture" "$WORK_UBNT/persistent/profiles/camp.cfg"
    backed_up=$(find "$WORK_UBNT/private-backups" -type f -path '*/live/camp.cfg' -print | sed -n '1p')
    [ -n "$backed_up" ] || fail 'profile backup was not created'
    assert_file_equal "$profile_fixture" "$backed_up"
    assert_no_live_stop
    assert_not_contains "$EVENT_LOG" "cfgmtd"
    [ ! -e "$DEVICE_ROOT/tmp/ubnt-wifi/paused" ] || fail 'stage-only created pause marker'
    [ -z "$(find "$DEVICE_ROOT/tmp" -maxdepth 1 -name 'ubnt-code-stage.*' -print)" ] || \
        fail 'stage-only left a remote stage behind'
}

case_activate() {
    printf '%s\n' 'ssh-rsa AAAATEST deployment@test.invalid' > "$WORK_UBNT/persistent/config/raspi_rsa_id.pub"
    expect_success --activate
    verify_installed_tree
    assert_file_equal "$profile_fixture" "$DEVICE_ROOT/etc/persistent/profiles/camp.cfg"
    grep -F 'ssh-rsa AAAAEXISTING' "$DEVICE_ROOT/etc/dropbear/authorized_keys" >/dev/null || \
        fail 'activation removed existing authorized key'
    grep -F 'ssh-rsa AAAATEST' "$DEVICE_ROOT/etc/dropbear/authorized_keys" >/dev/null || \
        fail 'activation did not install staged authorized key'
    [ -e "$DEVICE_ROOT/state/crond.running" ] || fail 'activation did not start cron'
    [ ! -e "$DEVICE_ROOT/tmp/ubnt-wifi/paused" ] || fail 'activation left paused marker'
    assert_order 'cfgmtd.*-w' 'cfgmtd.*-r'
    assert_order 'cfgmtd.*-r' 'md5sum.*readback.*/wifi_manager.sh'
    assert_order 'md5sum.*readback.*/wifi_manager.sh' 'sh-exec'
    assert_not_contains "$EVENT_LOG" 'pruned-rollbacks.tar.gz'
    "$python_bin" - "$DEVICE_ROOT/state/prospective.tar.gz" "$WORK_UBNT" <<'PY'
import pathlib
import sys
import tarfile
with tarfile.open(sys.argv[1]) as archive:
    names = archive.getnames()
    assert 'persistent/config/.profile' in names
    assert 'system.cfg' in names
    assert any('/rollback/code-' in name and name.endswith('/scripts/old.sh') for name in names)
    for source in pathlib.Path(sys.argv[2], 'persistent/scripts').glob('wifi_manager*.sh'):
        payload = archive.extractfile('persistent/scripts/' + source.name).read()
        assert payload == source.read_bytes()
PY
}

case_install_paused() {
    expect_success --install-paused
    verify_installed_tree
    [ ! -e "$DEVICE_ROOT/state/crond.running" ] || fail 'paused install restarted cron'
    [ -e "$DEVICE_ROOT/tmp/ubnt-wifi/paused" ] || fail 'paused install omitted pause marker'
    assert_contains "$EVENT_LOG" "pkill"
    if grep '^crond[[:space:]]' "$EVENT_LOG" >/dev/null; then
        fail 'paused install started cron'
    fi
}

case_keep_two() {
    make_rollback code-20240101T000001Z
    make_rollback code-20240101T000002Z
    make_rollback code-20240101T000003Z
    make_rollback profile-preserve
    make_rollback auth-preserve
    protected_profile=$(tree_digest "$DEVICE_ROOT/etc/persistent/rollback/profile-preserve")
    protected_auth=$(tree_digest "$DEVICE_ROOT/etc/persistent/rollback/auth-preserve")
    profile_before=$(tree_digest "$DEVICE_ROOT/etc/persistent/profiles")
    admin_before=$(tree_digest "$DEVICE_ROOT/tmp/system.cfg")
    authorized_before=$(tree_digest "$DEVICE_ROOT/etc/dropbear/authorized_keys")
    expect_success --install-paused --keep-rollbacks 2
    count=$(find "$DEVICE_ROOT/etc/persistent/rollback" -maxdepth 1 -type d -name 'code-*' | wc -l | tr -d ' ')
    [ "$count" -eq 2 ] || fail "expected 2 rollbacks, found $count"
    [ ! -e "$DEVICE_ROOT/etc/persistent/rollback/code-20240101T000001Z" ] || fail 'oldest rollback survived'
    [ ! -e "$DEVICE_ROOT/etc/persistent/rollback/code-20240101T000002Z" ] || fail 'second-oldest rollback survived'
    [ -d "$DEVICE_ROOT/etc/persistent/rollback/code-20240101T000003Z" ] || fail 'newest prior rollback was pruned'
    find "$DEVICE_ROOT/etc/persistent/rollback" -maxdepth 1 -type d -name 'code-2025*' | grep . >/dev/null || \
        fail 'new rollback was not installed'
    [ "$protected_profile" = "$(tree_digest "$DEVICE_ROOT/etc/persistent/rollback/profile-preserve")" ] || fail 'profile rollback was changed'
    [ "$protected_auth" = "$(tree_digest "$DEVICE_ROOT/etc/persistent/rollback/auth-preserve")" ] || fail 'auth rollback was changed'
    [ "$profile_before" = "$(tree_digest "$DEVICE_ROOT/etc/persistent/profiles")" ] || fail 'profiles changed during pruning'
    [ "$admin_before" = "$(tree_digest "$DEVICE_ROOT/tmp/system.cfg")" ] || fail 'admin config changed during pruning'
    [ "$authorized_before" = "$(tree_digest "$DEVICE_ROOT/etc/dropbear/authorized_keys")" ] || fail 'authorized keys changed during pruning'
}

case_repeated_bounded() {
    make_rollback code-20240101T000001Z
    for iteration in 1 2 3 4; do
        expect_success --install-paused --keep-rollbacks 2
        count=$(find "$DEVICE_ROOT/etc/persistent/rollback" -maxdepth 1 -type d -name 'code-*' | wc -l | tr -d ' ')
        [ "$count" -le 2 ] || fail "iteration $iteration retained $count rollbacks"
        : > "$DEVICE_ROOT/state/crond.running"
        rm -f "$DEVICE_ROOT/tmp/ubnt-wifi/paused"
    done
    assert_file_equal "$profile_fixture" "$DEVICE_ROOT/etc/persistent/profiles/camp.cfg"
}

case_oversized_preflight() {
    "$python_bin" - "$DEVICE_ROOT/etc/persistent/profiles/large.cfg" <<'PY'
import hashlib
import sys
with open(sys.argv[1], "wb") as handle:
    handle.write(b"wireless.1.ssid=Large Fake Profile\n")
    for counter in range(4_700):
        handle.write(hashlib.sha256(str(counter).encode("ascii")).digest())
PY
    before=$(tree_digest "$DEVICE_ROOT/etc/persistent")
    expect_failure --install-paused
    assert_contains "$DEPLOY_OUTPUT" 'prospective flash archive'
    [ "$before" = "$(tree_digest "$DEVICE_ROOT/etc/persistent")" ] || fail 'oversized preflight changed persistent storage'
    assert_no_live_stop
}

case_compression_failure() {
    before=$(tree_digest "$DEVICE_ROOT/etc/persistent")
    export FAKE_TAR_FAIL=compress
    expect_failure --install-paused
    [ "$before" = "$(tree_digest "$DEVICE_ROOT/etc/persistent")" ] || fail 'compression failure changed persistent storage'
    assert_no_live_stop
}

case_backup_pruned() {
    make_rollback code-20240101T000001Z
    make_rollback code-20240101T000002Z
    make_rollback code-20240101T000003Z
    expect_success --install-paused --keep-rollbacks 2 --backup-pruned
    archive=$(find "$WORK_UBNT/private-backups" -type f -name 'pruned-rollbacks.tar.gz' -print | sed -n '1p')
    [ -n "$archive" ] && [ -s "$archive" ] || fail 'verified pruned rollback archive is missing or empty'
    entries=$(/usr/bin/tar -tzf "$archive")
    printf '%s\n' "$entries" | grep 'code-20240101T000001Z' >/dev/null || fail 'backup omitted oldest pruned rollback'
    download_line=$(line_number "$EVENT_LOG" 'scp-download.*pruned-rollbacks.tar.gz')
    prune_line=$(line_number "$EVENT_LOG" "rm.*$DEVICE_ROOT/etc/persistent/rollback/code-20240101T000001Z")
    [ -n "$download_line" ] && [ -n "$prune_line" ] && [ "$download_line" -lt "$prune_line" ] || \
        fail 'live rollback was pruned before its backup was retrieved'
}

case_backup_scp_failure() {
    make_rollback code-20240101T000001Z
    make_rollback code-20240101T000002Z
    before=$(tree_digest "$DEVICE_ROOT/etc/persistent")
    export FAKE_SCP_FAIL_MATCH=pruned-rollbacks.tar.gz
    expect_failure --install-paused --keep-rollbacks 1 --backup-pruned
    [ "$before" = "$(tree_digest "$DEVICE_ROOT/etc/persistent")" ] || fail 'failed backup scp changed persistent storage'
    assert_no_live_stop
}

case_cfg_write_failure() {
    export FAKE_CFGMTD_WRITE_FAIL=exit
    expect_failure --activate
    assert_cron_restarted
    # Recovery must not create a daemon that ignores the next deployment's stop.
    unset FAKE_CFGMTD_WRITE_FAIL
    expect_success --install-paused
    [ ! -e "$DEVICE_ROOT/state/crond.running" ] || fail 'recovered cron could not be stopped'
}

case_cfg_write_segv() {
    export FAKE_CFGMTD_WRITE_FAIL=segv
    expect_failure --activate
    assert_cron_restarted
}

case_cfg_read_failure() {
    export FAKE_CFGMTD_READ_FAIL=1
    expect_failure --activate
    assert_cron_restarted
}

case_readback_mismatch() {
    export FAKE_CFGMTD_READBACK_CORRUPT=scripts/wifi_manager.sh
    expect_failure --activate
    assert_contains "$DEPLOY_OUTPUT" 'md5 mismatch'
    assert_cron_restarted
}

case_readback_missing() {
    # Leave an exact matching manager in the rollback to reject recursive checks.
    /bin/cp "$WORK_UBNT/persistent/scripts/wifi_manager.sh" "$DEVICE_ROOT/etc/persistent/scripts/wifi_manager.sh"
    export FAKE_CFGMTD_READBACK_MISSING=scripts/wifi_manager.sh
    expect_failure --activate
    assert_contains "$DEPLOY_OUTPUT" 'missing wifi_manager.sh'
    assert_cron_restarted
}

case_readback_symlink() {
    export FAKE_CFGMTD_READBACK_SYMLINK=1
    expect_failure --activate
    assert_contains "$DEPLOY_OUTPUT" 'symlinked code directory'
    assert_cron_restarted
}

case_post_stop_copy_failure() {
    export FAKE_CP_FAIL_MATCH="$DEVICE_ROOT/etc/persistent/profile.new"
    expect_failure --activate
    assert_cron_restarted
}

case_activation_failure() {
    export FAKE_CROND_FAIL_ONCE=1
    expect_failure --activate
    crond_count=$(grep -c '^crond' "$EVENT_LOG")
    [ "$crond_count" -ge 2 ] || fail 'activation failure did not retry cron startup'
    assert_cron_restarted
}

case_stale_plan() {
    export FAKE_MUTATE_AFTER_PREPARE=1
    expect_failure --install-paused
    assert_contains "$DEPLOY_OUTPUT" 'persistent tree changed since preflight'
    assert_no_live_stop
}

case_busy_lock() {
    mkdir -p "$DEVICE_ROOT/tmp/ubnt-wifi/lock"
    before=$(tree_digest "$DEVICE_ROOT/etc/persistent")
    expect_failure --install-paused
    [ "$before" = "$(tree_digest "$DEVICE_ROOT/etc/persistent")" ] || fail 'busy lock changed persistent storage'
    assert_no_live_stop
}

case_backup_checksum_failure() {
    make_rollback code-20240101T000001Z
    before=$(tree_digest "$DEVICE_ROOT/etc/persistent")
    export FAKE_SCP_CORRUPT=1
    expect_failure --install-paused --keep-rollbacks 1 --backup-pruned
    assert_contains "$DEPLOY_OUTPUT" 'backup checksum failed'
    [ "$before" = "$(tree_digest "$DEVICE_ROOT/etc/persistent")" ] || fail 'corrupt backup allowed live changes'
    assert_no_live_stop
}

case_fallback_crond() {
    export FAKE_CFGMTD_WRITE_FAIL=exit FAKE_HOOK_FAIL=1
    expect_failure --install-paused
    assert_contains "$DEPLOY_OUTPUT" 'crond fallback'
    assert_cron_restarted
}

case_signal_recovery() {
    export FAKE_CFGMTD_WRITE_FAIL="$current_case"
    expect_failure --activate
    assert_cron_restarted
}

case_direct_readback() {
    export FAKE_CFGMTD_LAYOUT=direct
    expect_success --activate
    assert_contains "$DEPLOY_OUTPUT" 'md5 verified'
}

case_ambiguous_readback() {
    export FAKE_CFGMTD_LAYOUT=ambiguous
    expect_failure --activate
    assert_contains "$DEPLOY_OUTPUT" 'ambiguous active flash'
    assert_cron_restarted
}

case_keep_one() {
    make_rollback code-20240101T000001Z
    expect_success --install-paused --keep-rollbacks 1
    [ ! -d "$DEVICE_ROOT/etc/persistent/rollback/code-20240101T000001Z" ] || fail 'keep-one retained old rollback'
    count=$(find "$DEVICE_ROOT/etc/persistent/rollback" -maxdepth 1 -type d -name 'code-*' | wc -l | tr -d ' ')
    [ "$count" -eq 1 ] || fail 'keep-one did not retain exactly the new rollback'
}

case_invalid_limits() {
    for limit in 0 -1 bad 99999; do
        expect_failure --activate --keep-rollbacks "$limit"
    done
    expect_failure --activate --max-bytes 114689
    [ ! -s "$EVENT_LOG" ] || fail 'invalid options contacted the device'
}

case_existing_pause() {
    mkdir -p "$DEVICE_ROOT/tmp/ubnt-wifi"
    printf 'owner maintenance hold\n' > "$DEVICE_ROOT/tmp/ubnt-wifi/paused"
    expect_success --activate
    assert_contains "$DEVICE_ROOT/tmp/ubnt-wifi/paused" 'owner maintenance hold'
    export FAKE_CFGMTD_WRITE_FAIL=exit
    expect_failure --activate
    assert_contains "$DEVICE_ROOT/tmp/ubnt-wifi/paused" 'owner maintenance hold'
    assert_cron_restarted
}

run_case() {
    current_case=$1
    test_function=$2
    setup_case "$current_case"
    "$test_function"
    case_count=$((case_count + 1))
}

/bin/bash -n "$ubnt_root/scp_to_device.sh"
/bin/dash -n "$ubnt_root/deploy_remote.sh"
/bin/dash -n "$remote_command_fixture"
/bin/dash -n "$0"

run_case stage-only case_stage_only
run_case activate case_activate
run_case install-paused case_install_paused
run_case keep-two case_keep_two
run_case repeated-bounded case_repeated_bounded
run_case oversized-preflight case_oversized_preflight
run_case compression-failure case_compression_failure
run_case backup-pruned case_backup_pruned
run_case backup-scp-failure case_backup_scp_failure
run_case cfg-write-failure case_cfg_write_failure
run_case cfg-write-segv case_cfg_write_segv
run_case cfg-read-failure case_cfg_read_failure
run_case readback-mismatch case_readback_mismatch
run_case readback-missing case_readback_missing
run_case readback-symlink case_readback_symlink
run_case post-stop-copy-failure case_post_stop_copy_failure
run_case activation-failure case_activation_failure
run_case stale-plan case_stale_plan
run_case busy-lock case_busy_lock
run_case backup-checksum-failure case_backup_checksum_failure
run_case fallback-crond case_fallback_crond
run_case HUP case_signal_recovery
run_case TERM case_signal_recovery
run_case PIPE case_signal_recovery
run_case direct-readback case_direct_readback
run_case ambiguous-readback case_ambiguous_readback
run_case keep-one case_keep_one
run_case invalid-limits case_invalid_limits
run_case existing-pause case_existing_pause

printf 'deployment: %s cases ok\n' "$case_count"
