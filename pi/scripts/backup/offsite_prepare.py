"""Destructive disk preparation with fresh whole-device evidence before writes."""
import datetime as dt
import json
from pathlib import Path
import re
import stat

from offsite_config import LABEL_PATTERN, MARKER, REPO_PATH, SCHEMA, MarkerRecord, valid_priority
from offsite_repository import durable_json, password, private_directory, records, register, validate_marker


COLUMNS = 'NAME,TYPE,SIZE,MODEL,SERIAL,TRAN,RO,LABEL,PARTLABEL,FSTYPE,MOUNTPOINTS,MAJ:MIN'


def flatten(nodes):
    for node in nodes:
        if not isinstance(node, dict) or not node.get('name') or not node.get('type'):
            raise RuntimeError('Incomplete block inventory')
        yield node
        yield from flatten(node.get('children', []))


def inventory(commands):
    value = json.loads(commands.run('lsblk', '--json', '--bytes', '--paths', '--output', COLUMNS))
    nodes = value.get('blockdevices')
    if not isinstance(nodes, list) or not nodes:
        raise RuntimeError('Empty block discovery; no disk can be erased')
    list(flatten(nodes))
    return nodes


def protected_labels(commands):
    labels = commands.run('mounts', 'policy').splitlines()
    if not labels or any(not re.fullmatch(LABEL_PATTERN, label) for label in labels):
        raise RuntimeError('Disk policy is unavailable or malformed')
    return set(labels)


def inspect_disk(repo, device, serial, show=False):
    cfg, commands = repo.cfg, repo.commands
    link = Path(device)
    if link.parent != cfg.by_id or not link.is_symlink():
        raise RuntimeError('--device must be a whole-disk /dev/disk/by-id symlink')
    resolved = link.resolve(strict=True)
    if cfg.block_devices and not stat.S_ISBLK(resolved.stat().st_mode):
        raise RuntimeError('Target is not a block device')
    commands.run('udevadm', 'settle', '--timeout=20')
    nodes = inventory(commands)
    matches = [node for node in nodes if node['name'] == str(resolved)]
    if len(matches) != 1 or matches[0]['type'] != 'disk':
        raise RuntimeError('Target does not identify exactly one whole disk')
    disk = matches[0]
    rows = list(flatten([disk]))
    if show:
        print(json.dumps(disk, indent=2), flush=True)
    if not disk.get('serial') or not disk.get('model') or not disk.get('size'):
        raise RuntimeError('Disk model, serial or size is missing')
    if serial != disk['serial']:
        raise RuntimeError('--erase-confirm must exactly match the displayed nonempty serial')
    if sum(node.get('serial') == serial for node in nodes if node['type'] == 'disk') != 1:
        raise RuntimeError('Serial is shared by multiple disks; refusing ambiguous USB identity')
    if disk.get('tran') != 'usb' or disk.get('ro') not in (False, 0):
        raise RuntimeError('Target must be a writable USB disk')
    offsite = commands.run('mounts', 'offsite').split()
    if repo.label not in offsite:
        raise RuntimeError('Label is not an offsite drive in disk_policy.sh OFFSITE_LABELS')
    policy = protected_labels(commands) | set(records(cfg, optional=True)['repos'])
    if repo.label in policy:
        raise RuntimeError('Requested label is protected by disk policy or known repositories')
    for row in rows:
        if 'mountpoints' not in row or not isinstance(row['mountpoints'], list):
            raise RuntimeError('Mount discovery is incomplete')
        if any(row['mountpoints']):
            raise RuntimeError('Target has a mounted partition or active swap; unmount it first')
        if row['type'] not in ('disk', 'part'):
            raise RuntimeError('Target has an active mapped child device')
        if row.get('label') in policy or row.get('partlabel') in policy:
            raise RuntimeError('Target contains a protected disk-policy label')
    all_rows = list(flatten(nodes))
    if any(row.get('label') == repo.label or row.get('partlabel') == repo.label for row in all_rows):
        raise RuntimeError('Requested label is already in use')
    target_names = {row['name'] for row in rows}
    for mount in ('/', '/boot', '/boot/firmware'):
        source = commands.run('findmnt', '-rn', '-T', mount, '-o', 'SOURCE').strip()
        if not source.startswith('/') or '\n' in source:
            raise RuntimeError('Cannot identify root/boot filesystem')
        source = str(Path(source).resolve(strict=True))
        if source not in {row['name'] for row in all_rows}:
            raise RuntimeError('Root/boot source is not in block inventory')
        if source in target_names:
            raise RuntimeError('Target holds the root or boot filesystem')
    return disk


def empty_mount_target(repo):
    target = repo.mount
    if target.is_symlink() or target.resolve() != target:
        raise RuntimeError('Unsafe target mount directory')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise RuntimeError('Target mount directory must be empty')


def new_partition(repo, old_disk):
    nodes = inventory(repo.commands)
    disks = [node for node in nodes if node['name'] == old_disk['name']]
    if len(disks) != 1:
        raise RuntimeError('Prepared disk disappeared')
    disk = disks[0]
    for key in ('name', 'serial', 'size', 'model', 'maj:min'):
        if disk.get(key) != old_disk.get(key):
            raise RuntimeError('Disk identity changed during partitioning')
    children = disk.get('children', [])
    if (len(children) != 1 or children[0]['type'] != 'part' or
            children[0].get('children') or any(disk.get('mountpoints', [True])) or
            any(children[0].get('mountpoints', [True]))):
        raise RuntimeError('Expected exactly one unmounted new partition')
    return children[0]['name']


def prepare(repo, device, serial, priority):
    if not valid_priority(priority):
        raise ValueError('Priority must be an integer from 0 to 2147483647')
    cfg, commands = repo.cfg, repo.commands
    disk = inspect_disk(repo, device, serial, show=True)
    empty_mount_target(repo)
    commands.run('restic', 'version')
    # Password/state validation precedes erasure. Never regenerate a lost password
    # while this Pi knows repositories encrypted with the original one.
    known = records(cfg, optional=True)
    password(cfg, create=not known['repos'])
    private_directory(cfg.state, cfg.owner)
    if inspect_disk(repo, device, serial) != disk:
        raise RuntimeError('Disk changed during preflight; nothing erased')
    commands.run('wipefs', '--all', '--', disk['name'])
    commands.run('sfdisk', '--wipe', 'always', disk['name'], input_text='label: gpt\n, , L\n')
    commands.run('udevadm', 'settle', '--timeout=20')
    partition = new_partition(repo, disk)
    # Debian 12 defaults already omit these features; make that compatibility
    # requirement explicit so a later e2fsprogs upgrade cannot surprise the NAS.
    commands.run('mkfs', '-L', repo.label, '-m', '0', '-O',
                 '^orphan_file,^metadata_csum_seed', partition, timeout=600)
    features = commands.run('tune2fs', '-l', partition)
    feature_lines = [line for line in features.splitlines() if line.startswith('Filesystem features:')]
    if len(feature_lines) != 1 or {'orphan_file', 'metadata_csum_seed'} & set(feature_lines[0].split()):
        raise RuntimeError('Cannot verify NAS-compatible ext4 features')
    commands.run('udevadm', 'settle', '--timeout=20')
    commands.run('mounts', 'mount', repo.label)
    repo.exact_mount()
    if repo.path.exists() or repo.path.is_symlink():
        raise RuntimeError('Repository directory unexpectedly exists after formatting')
    repo.path.mkdir(mode=0o700)
    repo.restic('init', timeout=120)
    repo_id = json.loads(repo.restic('cat', 'config')).get('id')
    marker: MarkerRecord = {'schema': SCHEMA, 'label': repo.label, 'repo_id': repo_id,
              'repo_path': REPO_PATH, 'priority': priority,
              'created': dt.datetime.now(dt.timezone.utc).isoformat()}
    validate_marker(marker, repo.label)
    repo.exact_mount()
    durable_json(repo.mount / MARKER, marker)
    # The watcher needs only this nonsecret marker; repository contents stay private.
    (repo.mount / MARKER).chmod(0o644)
    commands.run('sync', '-f', repo.mount)
    register(cfg, known, marker)
    print(json.dumps(marker, sort_keys=True), flush=True)
