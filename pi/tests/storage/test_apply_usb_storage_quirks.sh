#!/bin/bash
set -eu
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
. "$repo_root/pi/tests/lib.sh"
tool="$repo_root/pi/scripts/apply_usb_storage_quirks.py"
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

new_case() {
  case_dir="$tmp/$1"
  mkdir "$case_dir"
  cmdline="$case_dir/cmdline.txt"
}

apply() {
  python3 -I "$tool" --cmdline "$cmdline" "$@" > "$tmp/output" 2>&1
}

backup_count() {
  find "$case_dir" -name 'cmdline.txt.bak-*' | wc -l | tr -d ' '
}

assert_bytes() {
  cmp -s "$1" "$2" || fail "$3"
}

new_case absent
printf 'console=serial0,115200 root=PARTUUID=12345678-02 rootwait\n' > "$cmdline"
cp "$cmdline" "$tmp/original"
printf 'console=serial0,115200 root=PARTUUID=12345678-02 rootwait usb-storage.quirks=0bc2:2344:u\n' > "$tmp/expected"
apply --dry-run || fail 'absent-token dry run'
assert_bytes "$tmp/original" "$cmdline" 'dry run changed cmdline'
assert_eq 0 "$(backup_count)" 'dry run created backup'
grep -q '^--- .* (before)$' "$tmp/output" || fail 'missing before diff header'
grep -q '^+++ .* (after)$' "$tmp/output" || fail 'missing after diff header'
apply || fail 'absent-token apply'
assert_bytes "$tmp/expected" "$cmdline" 'absent-token result'
assert_eq 1 "$(backup_count)" 'apply needs one backup'
backup=$(find "$case_dir" -name 'cmdline.txt.bak-*')
assert_bytes "$tmp/original" "$backup" 'backup must be byte-exact'
python3 -I - "$backup" <<'PY' || fail 'UTC backup timestamp'
from pathlib import Path
import re
import sys
assert re.fullmatch(r'cmdline.txt.bak-\d{8}T\d{6}\.\d{6}Z', Path(sys.argv[1]).name)
PY
apply || fail 'repeat apply'
assert_bytes "$tmp/expected" "$cmdline" 'repeat apply changed content'
assert_eq 1 "$(backup_count)" 'no-op created another backup'
printf 'PASS: absent token, byte-exact backup, dry-run, repeat no-op\n'

apply --revert "$backup" --dry-run || fail 'revert dry-run'
assert_bytes "$tmp/expected" "$cmdline" 'revert dry-run changed cmdline'
apply --revert "$backup" || fail 'guarded revert'
assert_bytes "$tmp/original" "$cmdline" 'revert did not recover original bytes'
assert_eq 2 "$(backup_count)" 'revert must back up current cmdline'
printf 'root=PARTUUID=changed rootwait usb-storage.quirks=0bc2:2344:u\n' > "$cmdline"
cp "$cmdline" "$tmp/changed"
if apply --revert "$backup"; then fail 'reverted unrelated later edits'; fi
assert_bytes "$tmp/changed" "$cmdline" 'failed revert changed cmdline'
printf 'PASS: guarded revert and refusal after unrelated edits\n'

new_case merged
printf 'root=x usb-storage.quirks=0bda:9201:u,1234:abcd:s rootwait\n' > "$cmdline"
printf 'root=x usb-storage.quirks=0bda:9201:u,1234:abcd:s,0bc2:2344:u rootwait\n' > "$tmp/expected"
apply || fail 'merge different quirks'
assert_bytes "$tmp/expected" "$cmdline" 'different quirks were not preserved'
new_case flags
printf 'root=x usb_storage.quirks=0BC2:2344:st,1234:abcd:u rootwait\n' > "$cmdline"
printf 'root=x usb_storage.quirks=0BC2:2344:stu,1234:abcd:u rootwait\n' > "$tmp/expected"
apply || fail 'merge existing Seagate flags and underscore alias'
assert_bytes "$tmp/expected" "$cmdline" 'existing flags/spelling/order changed'
new_case present
printf '  root=x\tusb-storage.quirks=1234:abcd:s,0bc2:2344:su  rootwait \t\n' > "$cmdline"
cp "$cmdline" "$tmp/expected"
apply || fail 'already present'
assert_bytes "$tmp/expected" "$cmdline" 'already-present bytes changed'
assert_eq 0 "$(backup_count)" 'already present created backup'
printf 'PASS: other quirks, existing flags, alias, already-present no-op\n'

new_case preserve
printf ' \tconsole=x  root=PARTUUID=12345678-02\tfoo="a b" console=tty1 rootwait  \t\n' > "$cmdline"
printf ' \tconsole=x  root=PARTUUID=12345678-02\tfoo="a b" console=tty1 rootwait usb-storage.quirks=0bc2:2344:u  \t\n' > "$tmp/expected"
apply || fail 'preserve other tokens'
assert_bytes "$tmp/expected" "$cmdline" 'changed other tokens/quotes/whitespace'
new_case empty_value
printf 'root=x usb-storage.quirks= rootwait\n' > "$cmdline"
printf 'root=x usb-storage.quirks=0bc2:2344:u rootwait\n' > "$tmp/expected"
apply || fail 'empty existing quirk value'
assert_bytes "$tmp/expected" "$cmdline" 'empty quirk value not filled'
printf 'PASS: byte-preserved tokens, ordering, quotes, whitespace, empty value\n'

reject() {
  new_case "$1"
  printf '%b' "$2" > "$cmdline"
  cp "$cmdline" "$tmp/rejected"
  if apply; then fail "accepted $1"; fi
  assert_bytes "$tmp/rejected" "$cmdline" "refusal changed $1"
  assert_eq 0 "$(backup_count)" "refusal backed up $1"
  if apply --dry-run; then fail "dry run accepted $1"; fi
}
reject multiline 'root=x\nrootwait\n'
reject blank_second_line 'root=x\n\n'
reject no_final_newline 'root=x'
reject empty ''
reject blank ' \t\n'
reject crlf 'root=x\r\n'
reject nul 'root=x\0rootwait\n'
reject repeated_parameter 'root=x usb-storage.quirks=1234:5678:u usb_storage.quirks=0bc2:2344:u\n'
reject repeated_device 'root=x usb-storage.quirks=0bc2:2344:s,0BC2:2344:u\n'
reject malformed 'root=x usb-storage.quirks=garbage\n'
reject empty_entry 'root=x usb-storage.quirks=1234:5678:u,\n'
reject quoted_value 'root=x usb-storage.quirks="0bc2:2344:u"\n'
reject quoted_name 'root=x "usb-storage.quirks"=0bc2:2344:u\n'
reject bare_parameter 'root=x usb-storage.quirks\n'
reject init_separator 'root=x -- init\n'
reject unmatched_quotes 'root=x foo="a b\n'
long_flags=$(python3 -I -c 'print("a" * 110)')
reject quirk_overflow "root=x usb-storage.quirks=1234:5678:$long_flags\n"
long_root=$(python3 -I -c 'print("a" * 2040)')
reject cmdline_overflow "root=$long_root\n"
printf 'PASS: malformed/ambiguous inputs and kernel buffer overflow refused before writes\n'

new_case symlink
printf 'root=x\n' > "$tmp/link-target"
ln -s "$tmp/link-target" "$cmdline"
if apply; then fail 'accepted symlink'; fi
assert_eq 0 "$(backup_count)" 'symlink refusal created backup'
new_case hardlink
ln "$tmp/link-target" "$cmdline"
if apply; then fail 'accepted hardlink'; fi
assert_eq 0 "$(backup_count)" 'hardlink refusal created backup'
printf 'PASS: symlink and hardlink refusal\n'

# Exercise the public CLI with injected I/O failures, not private helper calls.
python3 -I - "$tool" "$tmp" <<'PY' || fail 'atomic write safeguards'
import contextlib
import io
import os
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

tool, root = sys.argv[1], Path(sys.argv[2])
for failure in ('directory-fsync', 'backup-fsync', 'stage-fsync', 'rename',
                'changed-source', 'post-rename-fsync', 'read-back'):
    directory = root / failure
    directory.mkdir()
    path = directory / 'cmdline.txt'
    original = b'root=x rootwait\n'
    path.write_bytes(original)
    path.chmod(0o640)
    real_fsync, real_replace = os.fsync, os.replace
    calls = []

    def fsync(fd):
        calls.append('fsync')
        count = calls.count('fsync')
        if (failure, count) in {('directory-fsync', 1), ('backup-fsync', 2),
                               ('stage-fsync', 4), ('post-rename-fsync', 5)}:
            raise OSError('injected fsync failure')
        if failure == 'changed-source' and count == 4:
            path.write_bytes(b'root=new rootwait\n')
        return real_fsync(fd)

    def replace(src, dst):
        calls.append('rename')
        if failure == 'rename':
            raise OSError('injected rename failure')
        replaced = real_replace(src, dst)
        if failure == 'read-back':
            path.write_bytes(b'root=tampered rootwait\n')
        return replaced

    sys.argv = [tool, '--cmdline', str(path)]
    with patch('os.fsync', fsync), patch('os.replace', replace):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                runpy.run_path(tool, run_name='__main__')
            except SystemExit as error:
                assert error.code == 1, (failure, error.code)
            else:
                raise AssertionError('CLI did not report failure')
    if failure == 'post-rename-fsync':
        assert path.read_bytes() == b'root=x rootwait usb-storage.quirks=0bc2:2344:u\n'
        assert calls == ['fsync'] * 4 + ['rename', 'fsync']
    elif failure == 'read-back':
        assert path.read_bytes() == b'root=tampered rootwait\n'
        assert next(directory.glob('cmdline.txt.bak-*')).read_bytes() == original
    else:
        expected = b'root=new rootwait\n' if failure == 'changed-source' else original
        assert path.read_bytes() == expected, failure
    if failure in ('directory-fsync', 'backup-fsync', 'stage-fsync', 'changed-source'):
        assert 'rename' not in calls, (failure, calls)
    assert not list(directory.glob('.cmdline.txt.*')), failure
    assert path.stat().st_mode & 0o777 == 0o640, failure
print('PASS: fsync/rename/read-back failures, concurrency guard, metadata, staged-file cleanup')
PY
python3 -I -B - "$tool" "$tmp" <<'PY' || fail 'runtime verification'
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('boot_quirk', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = Path(sys.argv[2]) / 'runtime'

def fixture(path, value):
    destination = root / path.lstrip('/')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(value)
    return destination

running = fixture('/proc/cmdline', 'root=x usb-storage.quirks=0bc2:2344:u\n')
parameter = fixture('/sys/module/usb_storage/parameters/quirks', '0bc2:2344:u\n')
mounts = {}
interfaces = []
vendors = []
for index, label in enumerate(('mbp2tbkup', 'movingparts', 'EXFAT512')):
    name = f'sd{chr(99 + index)}1'
    source = fixture('/dev/' + name, '')
    link = root / 'dev/disk/by-label' / label
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(source)
    usb = f'/sys/devices/usb2/2-{index + 1}'
    vendors.append(fixture(usb + '/idVendor', '0bc2\n'))
    fixture(usb + '/idProduct', '2344\n')
    interface = usb + f'/2-{index + 1}:1.0'
    fixture(interface + '/bInterfaceClass', '08\n')
    driver = root / 'sys/bus/usb/drivers/usb-storage'
    driver.mkdir(parents=True, exist_ok=True)
    driver_link = root / interface.lstrip('/') / 'driver'
    driver_link.symlink_to(driver, target_is_directory=True)
    interfaces.append(driver_link)
    block = root / interface.lstrip('/') / f'host/target/block/{name[:-1]}/{name}'
    block.mkdir(parents=True)
    class_link = root / 'sys/class/block' / name
    class_link.parent.mkdir(parents=True, exist_ok=True)
    class_link.symlink_to(block, target_is_directory=True)
    target = '/mnt/' + label
    mounts[target] = {'source': str(source), 'target': target,
                      'fstype': 'ext4', 'options': 'rw,relatime'}

def local_path(value):
    if str(value).startswith(('/proc/', '/sys/', '/dev/')):
        return root / str(value).lstrip('/')
    return Path(value)

def findmnt(argv, **kwargs):
    assert argv[:3] == ['/usr/bin/findmnt', '--json', '--mountpoint'], argv
    return json.dumps({'filesystems': [mounts[argv[3]]]})

def verify(accepted):
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with patch.object(module, 'Path', local_path), patch.object(module.subprocess, 'check_output', findmnt):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                status = module.main(['--verify'])
            except ValueError:
                assert not accepted
            else:
                assert accepted and status == 0
    assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}

verify(True)
parameter.write_text('\n')
verify(False)
parameter.write_text('0bc2:2344:u\n')
running.write_text('root=x\n')
verify(False)
running.write_text('root=x usb-storage.quirks=0bc2:2344:u\n')
uas = root / 'sys/bus/usb/drivers/uas'
uas.mkdir()
interfaces[0].unlink()
interfaces[0].symlink_to(uas, target_is_directory=True)
verify(False)
interfaces[0].unlink()
interfaces[0].symlink_to(driver, target_is_directory=True)
vendors[0].write_text('1234\n')
verify(False)
vendors[0].write_text('0bc2\n')
mounts['/mnt/EXFAT512']['options'] = 'ro,relatime'
verify(False)
mounts['/mnt/EXFAT512']['options'] = 'rw,relatime'
mounts['/mnt/movingparts']['source'] = str(source)
verify(False)
print('PASS: runtime cmdline/module flags, USB identities/drivers, mount sources/rw; read-only')
PY
printf 'All USB-storage boot quirk tests passed.\n'
