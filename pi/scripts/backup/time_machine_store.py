"""Immutable encrypted band objects, generation manifests and offline restore.

No network, mounts or device commands. Source files are COPIED, never linked:
Time Machine modifies bands in place. Only immutable private objects are reused.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import tempfile
import time

OWNER = 'vanpi-time-machine-icloud-v1'
GENERATION = re.compile(r'tm-\d{8}T\d{6}Z-[0-9a-f]{8}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
BUNDLE = 'm4mac0.sparsebundle'
CAPTURE_TEMP_PREFIX = '.capture-'


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, sort_keys=True)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def relative_path(value):
    if (not isinstance(value, str) or not value or value.startswith('/') or
            str(PurePosixPath(value)) != value or
            any(p in ('.', '..') for p in PurePosixPath(value).parts) or
            any(ord(c) < 32 for c in value) or '\\' in value):
        raise ValueError('unsafe manifest path')
    return value


def signature(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('non-regular image/object file')
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('unsafe image root')
    result, directories = {}, []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            p = Path(directory) / name
            if p.is_symlink():
                raise ValueError('symlink in image')
            directories.append(relative_path(p.relative_to(root).as_posix()))
        for name in files:
            p = Path(directory) / name
            rel = relative_path(p.relative_to(root).as_posix())
            if rel == 'lock':  # Runtime lock state is not a recovery artifact.
                continue
            result[rel] = signature(p)
    return result, sorted(directories)


def source_evidence(source, max_age_hours, now=None):
    now = time.time() if now is None else now
    with (source / 'Info.plist').open('rb') as f:
        info = plistlib.load(f)
    if info.get('band-size') != 67108864 or info.get('bundle-backingstore-version') != 2:
        raise ValueError('unexpected Time Machine image geometry')
    # This deployment was independently confirmed encrypted by macOS hdiutil.
    # Refuse an accidental replacement lacking the observed encrypted-image header.
    with (source / 'token').open('rb') as f:
        if f.read(8) != b'encrcdsa':
            raise ValueError('expected encrypted Time Machine image header')
    with (source / 'com.apple.TimeMachine.SnapshotHistory.plist').open('rb') as f:
        history = plistlib.load(f)
    dates = []
    for row in history.get('Snapshots', []):
        value = row.get('com.apple.backupd.SnapshotCompletionDate')
        if isinstance(value, dt.datetime):
            dates.append(value.replace(tzinfo=dt.timezone.utc).timestamp() if value.tzinfo is None else value.timestamp())
    if not dates or not 0 <= now - max(dates) <= max_age_hours * 3600:
        raise ValueError('completed Time Machine snapshot is missing or stale')
    return {'last_backup_at': max(dates), 'band_size': info['band-size'], 'encrypted': True}


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or manifest.get('owner') != OWNER or
            not GENERATION.fullmatch(manifest.get('generation', '')) or
            manifest.get('bundle') != BUNDLE or manifest.get('encrypted') is not True):
        raise ValueError('invalid Time Machine manifest identity')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files or len(files) > 1000000:
        raise ValueError('invalid manifest file collection')
    objects = {}
    for path, row in files.items():
        relative_path(path)
        if (not isinstance(row, dict) or not DIGEST.fullmatch(row.get('sha256', '')) or
                type(row.get('bytes')) is not int or row['bytes'] < 0):
            raise ValueError('invalid manifest digest or size')
        digest = row['sha256']
        if digest in objects and objects[digest]['bytes'] != row['bytes']:
            raise ValueError('conflicting manifest object sizes')
        objects[digest] = {'bytes': row['bytes'], 'sha256': digest}
    for path in manifest.get('directories', []):
        relative_path(path)
        if path in files:
            raise ValueError('manifest file/directory collision')
    for path in files:
        if any(str(p) in files for p in PurePosixPath(path).parents if str(p) != '.'):
            raise ValueError('manifest path traversal through file')
    if not {'Info.plist', 'token', 'com.apple.TimeMachine.SnapshotHistory.plist'} <= files.keys() or not any(p.startswith('bands/') for p in files):
        raise ValueError('incomplete Time Machine manifest')
    return objects


def copy_object(source, objects, check):
    before = signature(source)
    fd, name = tempfile.mkstemp(prefix=CAPTURE_TEMP_PREFIX, dir=objects)
    sha = hashlib.sha256()
    try:
        with source.open('rb') as inp, os.fdopen(fd, 'wb') as out:
            while True:
                chunk = inp.read(4 * 1024 * 1024)
                if not chunk:
                    break
                check(); sha.update(chunk); out.write(chunk)
            out.flush(); os.fsync(out.fileno())
        if signature(source) != before:
            raise RuntimeError('image changed during capture')
        digest = sha.hexdigest()
        dest = objects / digest
        # A full fresh copy proves this digest even when recovering a damaged
        # local object. Replacement never touches the live image or cloud copy.
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise ValueError('unsafe immutable object path')
        os.chmod(name, 0o400)
        os.replace(name, dest)
        return {'bytes': before[2], 'sha256': digest}, signature(dest)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def freeze(source, root, name, max_age_hours, min_free_bytes, check, report):
    if not GENERATION.fullmatch(name):
        raise ValueError('invalid generation')
    before, dirs = inventory(source)
    evidence = source_evidence(source, max_age_hours)
    objects = root / 'objects'
    objects.mkdir(mode=0o700, exist_ok=True)
    if objects.is_symlink():
        raise ValueError('unsafe object directory')
    index_path = root / 'capture-index.json'
    old = read_json(index_path, {})
    index, files = {}, {}
    total, done = sum(v[2] for v in before.values()), 0
    try:
        for count, (rel, sig) in enumerate(sorted(before.items())):
            check()
            cached = old.get(rel, {})
            row = cached.get('entry', {})
            obj = objects / row.get('sha256', '_invalid')
            reuse = False
            if cached.get('source') == sig and DIGEST.fullmatch(row.get('sha256', '')) and obj.exists():
                reuse = signature(obj) == cached.get('object') and row.get('bytes') == sig[2]
            if reuse:
                obj_sig = signature(obj)
            else:
                if shutil.disk_usage(root).free < sig[2] + min_free_bytes:
                    raise RuntimeError('insufficient staging disk headroom')
                row, obj_sig = copy_object(source / rel, objects, check)
            index[rel] = {'source': sig, 'entry': row, 'object': obj_sig}
            files[rel] = row
            done += sig[2]
            report(done, total, count + 1, len(before))
            if count % 32 == 0:
                atomic_json(index_path, {**old, **index})
        check()
        if inventory(source) != (before, dirs):
            raise RuntimeError('image changed across capture; no generation published')
        manifest = {'owner': OWNER, 'generation': name, 'bundle': BUNDLE,
                    'created_at': time.time(), 'directories': dirs, 'files': files, **evidence}
        validate_manifest(manifest)
        atomic_json(root / 'pending.json', manifest)
        return manifest
    finally:
        atomic_json(index_path, {**old, **index})


def restore(store, name, destination):
    """Offline restore into a NEW bundle, never hard-linking writable bands."""
    if not GENERATION.fullmatch(name) or destination.exists() or destination.is_symlink():
        raise ValueError('select a valid generation and a new destination')
    gen = store / 'generations' / name
    manifest_bytes = (gen / 'manifest.json').read_bytes()
    marker = read_json(gen / '_COMPLETE.json')
    if (not marker or marker.get('owner') != OWNER or marker.get('generation') != name or
            marker.get('verification') != 'sha256-download-compared' or
            marker.get('manifest_sha256') != hashlib.sha256(manifest_bytes).hexdigest()):
        raise ValueError('missing or mismatched verified completion marker')
    manifest = json.loads(manifest_bytes)
    validate_manifest(manifest)
    if manifest['generation'] != name:
        raise ValueError('manifest generation mismatch')
    required = sum(v['bytes'] for v in manifest['files'].values())
    if shutil.disk_usage(destination.parent).free < required + 1024**3:
        raise RuntimeError('insufficient restore destination space')
    temporary = Path(tempfile.mkdtemp(prefix='.' + destination.name + '.restoring-', dir=destination.parent))
    try:
        for directory in manifest['directories']:
            (temporary / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        for rel, entry in manifest['files'].items():
            src = store / 'objects' / entry['sha256']
            if signature(src)[2] != entry['bytes']:
                raise ValueError('wrong object size: ' + entry['sha256'])
            dest = temporary / rel
            dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            sha = hashlib.sha256()
            with src.open('rb') as inp, dest.open('xb') as out:
                for data in iter(lambda: inp.read(4 * 1024 * 1024), b''):
                    sha.update(data); out.write(data)
            dest.chmod(0o600)
            if sha.hexdigest() != entry['sha256']:
                raise ValueError('object SHA-256 mismatch: ' + entry['sha256'])
        if destination.exists():
            raise ValueError('destination appeared during restore')
        os.rename(temporary, destination)
    except BaseException:
        # Keep partial restore for inspection; never recursively remove a path.
        print('Partial restore retained at ' + str(temporary))
        raise
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Restore a verified iCloud Time Machine store downloaded with rclone.')
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    print(restore(args.store, args.generation, args.destination))
