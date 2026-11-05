"""Subprocess command double for offsite entry-point behavior tests; no device I/O.

The `mounts` double honors offsite_mounts.sh's admission and release contract
(OFFSITE.md "Locks"): it checks the inherited descriptors and probes the real
fixture registry, so lock ordering mistakes fail these tests.
"""
# Every call is a fresh interpreter (the fixtures start it with -S) and the
# suites make thousands, so modules only a few commands need load where used.
import fcntl
import json
import os
from pathlib import Path
import sys
import time


# Snapshot helper paths installed over a fixture live tree by the isolation test.
ALIASES = {'offsite_mounts.sh': 'mounts', 'samba_share_control.sh': 'samba', 'ntfy_send.sh': 'notify'}
LOGGED_ENVIRONMENT = ('OFFSITE_ADMISSION', 'VANPI_BACKUP_JOB_LOCK_FD', 'VANPI_BACKUP_DISK_LOCK_FDS',
                      'VANPI_BACKUP_CLOUD_SLOT_FD')
# Upper bounds only, so a failed test never leaves a fixture process behind for long.
HOLD_SECONDS = 300
ORPHAN_SECONDS = 120


def main():
    fixture = Path(os.environ['OFFSITE_FIXTURE'])
    state = json.loads(fixture.read_text())
    executable = Path(sys.argv[0]).name
    name, args = ALIASES.get(executable, executable.removeprefix('fake-')), sys.argv[1:]
    with (fixture.parent / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps([name, *args]) + '\n')
    with (fixture.parent / 'executions.jsonl').open('a') as stream:
        stream.write(json.dumps({'name': name, 'args': args, 'path': sys.argv[0],
                                 'env': {key: os.environ.get(key) for key in LOGGED_ENVIRONMENT}}) + '\n')
    if state.get('fail_command') == name:
        print('fixture command failure', flush=True)
        return 7
    commands = {'lsblk': lsblk, 'findmnt': findmnt, 'mounts': mounts, 'sfdisk': partition,
                'mkfs': mkfs, 'tune2fs': features, 'restic': restic, 'smbstatus': smbstatus,
                'samba': samba, 'notify': notify}
    result = commands.get(name, lambda s, a: 0)(state, args)
    if name in ('sfdisk', 'mkfs', 'samba', 'notify') or (name == 'mounts' and args[0] in ('mount', 'release')):
        temporary = fixture.with_name(f'{fixture.name}.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(state))
        os.replace(temporary, fixture)
    return result or 0


def lsblk(state, args):
    print(json.dumps({'blockdevices': state['disks']}))


def all_rows(state):
    return [row for disk in state['disks'] for row in [disk, *disk.get('children', [])]]


def findmnt(state, args):
    mapping = state['mounts']
    if '--json' in args:
        print(json.dumps({'filesystems': [{'target': k, 'source': v} for k, v in mapping.items()]}))
        return
    if '-S' in args:
        matches = [k for k, v in mapping.items() if v == args[args.index('-S') + 1]]
    elif '-M' in args:
        matches = [mapping[args[args.index('-M') + 1]]] if args[args.index('-M') + 1] in mapping else []
    else:
        target = args[args.index('-T') + 1]
        targets = sorted((p for p in mapping if target == p or target.startswith(p.rstrip('/') + '/')), key=len)
        column = args[args.index('-o') + 1] if '-o' in args else 'SOURCE'
        matches = [targets[-1] if column == 'TARGET' else mapping[targets[-1]]] if targets else []
    if not matches:
        return 1
    print('\n'.join(matches))


def holds(fd, path, exclusive):
    try:
        actual, expected = os.fstat(fd), path.lstat()
    except OSError:
        return False
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        return False
    if not sys.platform.startswith('linux'):
        return True  # Darwin has no fdinfo: descriptor identity only.
    import backup_locks
    try:
        backup_locks.require_held(fd, path, exclusive=exclusive)
    except (OSError, RuntimeError):
        return False
    return True


def admitted(args):
    """offsite_mounts.sh: `job` needs job-offsite (and for mount the label's shared disk fd)."""
    mode, registry = os.environ.get('OFFSITE_ADMISSION'), Path(os.environ['VANPI_BACKUP_LOCK_DIR'])
    if mode == 'prepare':
        return holds(9, Path(os.environ['VANPI_BACKUP_JOB_LOCK']), True)
    if mode != 'job' or not holds(int(os.environ['VANPI_BACKUP_JOB_LOCK_FD']), registry / 'job-offsite.lock', True):
        return False
    if args[0] != 'mount':
        return True
    disks = dict(item.split('=') for item in os.environ.get('VANPI_BACKUP_DISK_LOCK_FDS', '').split(',') if item)
    return args[1] in disks and holds(int(disks[args[1]]), registry / f'disk-{args[1]}.lock', False)


def stall(record):
    """A release that blocks (a stuck unmount) until the test creates record['proceed']."""
    Path(record['stalled']).touch()
    deadline = time.monotonic() + HOLD_SECONDS
    while not Path(record['proceed']).exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def released(state, label, target):
    """0 released, 3 another holder, 4 verified absent, 1 failure (the helper's exit codes)."""
    if state.get('release_stall', {}).get('label') == label:
        stall(state['release_stall'])
    if state.get('drained_before_release') == label:
        state['mounts'].pop(target, None)  # An ignition drain won the race for the disk.
    if target not in state['mounts']:
        return 4
    if state.get('release_failure') == label:
        print('fixture unmount failure')
        return 1
    fd = os.open(Path(os.environ['VANPI_BACKUP_LOCK_DIR']) / f'disk-{label}.lock', os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 3
    finally:
        os.close(fd)
    state['mounts'].pop(target)
    state.setdefault('released', []).append(label)
    return 0


def mounts(state, args):
    if args[0] in ('policy', 'offsite'):
        print('\n'.join(state[args[0]]))
        return
    label = args[1]
    if state.get('source_conf') and args[0] in ('mount', 'release') and not sourced_configuration(state):
        return 1
    if args[0] in ('mount', 'release') and not admitted(args):
        print('fixture mount helper refused admission')
        return 1
    rows = [row for row in all_rows(state) if row.get('label') == label]
    if len(rows) != 1:
        return 1
    target = str(Path(state['mount_root']) / label)
    if args[0] == 'resolve':
        print(rows[0]['name'])
    elif args[0] == 'mount':
        Path(target).mkdir(exist_ok=True)
        state['mounts'][target] = rows[0]['name']
    elif args[0] == 'release':
        return released(state, label, target)


def partition(state, args):
    # sfdisk reads its script to EOF; replying first can break the supervisor's pipe write.
    sys.stdin.read()
    for disk in state['disks']:
        if disk['name'] == args[-1]:
            path = disk['name'] + '1'
            Path(path).touch()
            disk['children'] = [{'name': path, 'type': 'part', 'label': None, 'partlabel': None,
                                 'fstype': None, 'mountpoints': [None]}]


def mkfs(state, args):
    for row in all_rows(state):
        if row['name'] == args[-1]:
            row.update(label=args[args.index('-L') + 1], fstype='ext4')


def features(state, args):
    print('Filesystem features: has_journal extent metadata_csum 64bit')


def restic(state, args):
    if args == ['version']:
        print('restic 0.19.1')
    elif 'init' in args:
        print('created restic repository')
    elif 'cat' in args:
        print(json.dumps({'id': state.get('repo_id', 'a' * 64)}))
    elif 'backup' in args and '--stdin' in args:
        receipt = Path(state['mount_root']).parent / 'receipt.json'
        receipt.write_text(sys.stdin.read())
        print(json.dumps({'message_type': 'summary', 'snapshot_id': 'd' * 64}))
    elif 'snapshots' in args:
        source = Path(state['mount_root']).parent.joinpath('snapshot-source').read_text()
        is_receipt = args[-1] == 'd' * 64
        print(json.dumps([{'id': ('d' if is_receipt else 'b') * 64, 'hostname': 'vanpi',
                           'tags': ['offsite-receipt-v1'] if is_receipt else [source, 'offsite-incomplete']}]))
    elif 'dump' in args:
        print(Path(state['mount_root']).parent.joinpath('receipt.json').read_text())
    elif 'backup' in args:
        Path(state['mount_root']).parent.joinpath('snapshot-source').write_text(args[args.index('--tag') + 1])
        live = state.get('move_live')
        if live and Path(live).exists():
            os.rename(live, live + '.moved')  # A deployment replaced the whole live tree mid-run.
        events = state.get('progress_events', [{'message_type': 'status', 'bytes_done': 25,
                                              'total_bytes': 100, 'seconds_elapsed': 5}])
        for event in events:
            print(json.dumps(event), flush=True)
        if state.get('orphan'):
            spawn_orphan(Path(state['orphan']))
        Path(state['mount_root']).parent.joinpath('backup-started').touch()
        hold = state.get('hold_until')
        deadline = time.monotonic() + HOLD_SECONDS
        while hold and not Path(hold).exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if state.get('hang'):
            while True:
                time.sleep(0.1)
        if state.get('change_image'):
            image = Path(state['mount_root']) / 'mbp2tbkup' / 'm4mac0.sparsebundle' / 'sample'
            image.write_text('changed while restic was reading')
        if state.get('backup_failure'):
            print('unable to read source: Input/output error')
            return 3
        print(json.dumps({'message_type': 'summary', 'snapshot_id': 'b' * 64,
                          'total_bytes_processed': 100}))
    elif 'tag' in args:
        print(json.dumps({'message_type': 'changed', 'old_snapshot_id': 'b' * 64,
                          'new_snapshot_id': 'c' * 64}))
        print(json.dumps({'message_type': 'summary', 'changed_snapshots': 1}))
    elif 'check' in args:
        print('no errors were found')
    elif 'restore' in args:
        restored = Path(args[args.index('--target') + 1] + args[args.index('--include') + 1])
        restored.parent.mkdir(parents=True, exist_ok=True)
        restored.write_text('content')
        print('restored 1 file')


def spawn_orphan(record):
    """A descendant in its own session that keeps restic's inherited lock descriptors and ignores TERM.

    Its standard streams go to /dev/null so the worker's pipe reaches EOF; only
    the lock descriptors outlive restic, as with a real orphaned child.
    """
    import signal
    if os.fork():
        return
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    null = os.open(os.devnull, os.O_RDWR)
    for stream in (0, 1, 2):
        os.dup2(null, stream)
    os.close(null)
    temporary = record.with_name(record.name + '.tmp')
    temporary.write_text(str(os.getpid()))
    os.replace(temporary, record)
    time.sleep(ORPHAN_SECONDS)
    os._exit(0)


def sourced_configuration(state):
    """Source backup_conf.sh beside this helper first, as offsite_mounts.sh does; False if it fails."""
    import subprocess
    here = Path(sys.argv[0]).resolve().parent
    result = subprocess.run(['/bin/bash', '-c', '. "$1" || exit 1; printf "%s\\n" "$BACKUP_CONF_DIR" "$KEEP_DAILY"',
                             'conf', str(here / 'backup_conf.sh')], capture_output=True, text=True, timeout=60)
    lines = result.stdout.splitlines()
    live = state.get('move_live')
    state.setdefault('sourced_conf', []).append({
        'status': result.returncode, 'directory': lines[0] if lines else None,
        'keep_daily': lines[1] if len(lines) > 1 else None, 'stderr': result.stderr[-500:],
        'live_present': Path(live).exists() if live else None})
    return result.returncode == 0


def smbstatus(state, args):
    source = Path(state['mount_root']) / 'mbp2tbkup' / 'm4mac0.sparsebundle'
    handles = {'1': {'service_path': str(source.parent), 'filename': source.name + '/bands/0'}} if state.get('handles') else {}
    print(json.dumps({'open_files': handles, 'tcons': {'1': {'service': 'mbp2tbkup'}} if state.get('trees') else {}}))


def notify(state, args):
    """Record which registry locks are still held and, from a snapshot, verify its manifest."""
    registry = Path(os.environ['VANPI_BACKUP_LOCK_DIR'])
    busy = []
    for path in sorted(registry.glob('*.lock')):
        if path.name.startswith('job-'):
            continue
        fd = os.open(path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            busy.append(path.name)
        finally:
            os.close(fd)
    state['busy_at_notify'] = busy
    root = Path(sys.argv[0]).parent
    if (root / 'manifest.json').exists():
        import hashlib
        manifest = json.loads((root / 'manifest.json').read_text())
        state['manifest_ok'] = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
                                   for name, digest in manifest['files'].items())


def samba(state, args):
    if args[0] == 'close':
        state['trees'] = False


if __name__ == '__main__':
    raise SystemExit(main())
