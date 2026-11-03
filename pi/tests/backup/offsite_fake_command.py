"""Subprocess command double for offsite entry-point behavior tests; no device I/O."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def main():
    fixture = Path(os.environ['OFFSITE_FIXTURE'])
    state = json.loads(fixture.read_text())
    name, args = Path(sys.argv[0]).name.removeprefix('fake-'), sys.argv[1:]
    with (fixture.parent / 'commands.jsonl').open('a') as stream:
        stream.write(json.dumps([name, *args]) + '\n')
    if state.get('fail_command') == name:
        print('fixture command failure', flush=True)
        return 7
    commands = {'lsblk': lsblk, 'findmnt': findmnt, 'mounts': mounts, 'sfdisk': partition,
                'mkfs': mkfs, 'tune2fs': features, 'restic': restic, 'smbstatus': smbstatus,
                'samba': samba, 'umount': umount}
    result = commands.get(name, lambda s, a: 0)(state, args)
    if name in ('sfdisk', 'mkfs', 'samba', 'umount') or (name == 'mounts' and args[0] in ('mount', 'release')):
        with tempfile.NamedTemporaryFile(mode='w', dir=fixture.parent, delete=False) as stream:
            json.dump(state, stream)
        os.replace(stream.name, fixture)
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
        matches = [mapping[targets[-1]]] if targets else []
    if not matches:
        return 1
    print('\n'.join(matches))


def mounts(state, args):
    if args[0] in ('policy', 'offsite'):
        print('\n'.join(state[args[0]]))
        return
    label = args[1]
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
        state['mounts'].pop(target)


def partition(state, args):
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
    elif 'backup' in args:
        print(json.dumps({'message_type': 'status', 'bytes_done': 25, 'total_bytes': 100,
                          'seconds_elapsed': 5}), flush=True)
        Path(state['mount_root']).parent.joinpath('backup-started').touch()
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


def smbstatus(state, args):
    source = Path(state['mount_root']) / 'mbp2tbkup' / 'm4mac0.sparsebundle'
    handles = {'1': {'service_path': str(source.parent), 'filename': source.name + '/bands/0'}} if state.get('handles') else {}
    print(json.dumps({'open_files': handles, 'tcons': {'1': {'service': 'mbp2tbkup'}} if state.get('trees') else {}}))


def samba(state, args):
    if args[0] == 'close':
        state['trees'] = False


def umount(state, args):
    state['mounts'].pop(args[-1])


if __name__ == '__main__':
    raise SystemExit(main())
