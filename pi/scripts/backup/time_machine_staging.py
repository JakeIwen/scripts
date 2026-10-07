"""Collect unreachable local capture objects while the backup lock is held.

Only validated SHA-256 files inside the owned objects directory are candidates.
Neither cloud objects, the live sparsebundle, nor archived images are touched.
"""
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import stat

import time_machine_store as store

JOB_LOCK = Path('/run/lock/vanpi_backup.lock')
MAX_METADATA_BYTES = 64 * 1024**2
ROOT_FILES = {'OWNER.json', 'capture-index.json', 'pending.json', 'retiring.json',
              'verified-objects.json', 'upload-files.txt'}


@dataclass(frozen=True)
class ObjectCandidate:
    name: str
    signature: tuple
    allocated_bytes: int


@dataclass(frozen=True)
class CleanupPlan:
    root: Path
    directories: tuple
    metadata: tuple
    objects_identity: tuple
    protected_count: int
    candidates: tuple[ObjectCandidate, ...]

    def summary(self):
        return {'objects': len(self.candidates),
                'allocated_bytes': sum(row.allocated_bytes for row in self.candidates),
                'protected_objects': self.protected_count}


def require_lock(fd, path):
    actual, expected = os.fstat(fd), path.lstat()
    if (not stat.S_ISREG(expected.st_mode) or not stat.S_ISREG(actual.st_mode)
            or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)):
        raise RuntimeError('staging cleanup requires the existing backup lock descriptor')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def directory_signature(path, owner=None):
    value = path.lstat()
    if (not stat.S_ISDIR(value.st_mode) or value.st_mode & 0o077
            or (owner is not None and value.st_uid != owner)):
        raise ValueError('unsafe staging directory')
    return (value.st_dev, value.st_ino, value.st_mtime_ns, value.st_ctime_ns)


def unique_metadata_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate staging reference key')
        result[key] = value
    return result


def read_metadata(path, metadata, owner, default=None):
    try:
        signature = tuple(store.signature(path))
    except FileNotFoundError:
        metadata[path] = None
        return default
    value = path.lstat()
    if value.st_uid != owner or value.st_mode & 0o022 or value.st_size > MAX_METADATA_BYTES:
        raise ValueError('unsafe staging reference metadata')
    raw = path.read_bytes()
    if tuple(store.signature(path)) != signature:
        raise RuntimeError('staging references changed during inspection')
    metadata[path] = signature
    return raw.decode() if path.name == 'upload-files.txt' else json.loads(raw, object_pairs_hook=unique_metadata_keys)


def cache_references(index):
    if not isinstance(index, dict):
        raise ValueError('invalid capture index; cleanup refused')
    referenced = set()
    for relative, cached in index.items():
        store.relative_path(relative)
        if not isinstance(cached, dict) or not isinstance(cached.get('entry'), dict):
            raise ValueError('invalid capture index entry')
        entry = cached['entry']
        if (not isinstance(entry.get('sha256'), str) or not store.DIGEST.fullmatch(entry['sha256'])
                or type(entry.get('bytes')) is not int or entry['bytes'] < 0):
            raise ValueError('invalid capture index object')
        for field in ('source', 'object'):
            signature = cached.get(field)
            if (not isinstance(signature, list) or len(signature) != 5
                    or any(type(v) is not int or v < 0 for v in signature)
                    or signature[2] != entry['bytes']):
                raise ValueError('invalid capture index signature')
        referenced.add(entry['sha256'])
    return referenced


def manifest_references(manifest, expected_name=None):
    refs = store.validate_manifest(manifest)
    if expected_name is not None and manifest['generation'] != expected_name:
        raise ValueError('local generation identity mismatch')
    return set(refs)


def reference_state(root):
    owner = root.lstat().st_uid
    directories = {root: directory_signature(root)}
    metadata = {}
    if not {p.name for p in root.iterdir()} <= ROOT_FILES | {'objects', 'generations'}:
        raise ValueError('unknown staging contents; cleanup refused')
    if read_metadata(root / 'OWNER.json', metadata, owner) != {'owner': store.OWNER}:
        raise ValueError('staging owner mismatch')
    index = read_metadata(root / 'capture-index.json', metadata, owner)
    refs = cache_references(index) if index is not None else set()
    pending = read_metadata(root / 'pending.json', metadata, owner)
    if pending is not None:
        refs.update(manifest_references(pending))
    generations = root / 'generations'
    directories[generations] = directory_signature(generations, owner)
    for generation in sorted(generations.iterdir()):
        if not store.GENERATION.fullmatch(generation.name):
            raise ValueError('unknown local generation')
        directories[generation] = directory_signature(generation, owner)
        if not {p.name for p in generation.iterdir()} <= {'manifest.json', '_COMPLETE.json'}:
            raise ValueError('unknown generation contents')
        manifest = read_metadata(generation / 'manifest.json', metadata, owner)
        refs.update(manifest_references(manifest, generation.name))
        read_metadata(generation / '_COMPLETE.json', metadata, owner)
    verified = read_metadata(root / 'verified-objects.json', metadata, owner, {})
    if not isinstance(verified, dict) or any(not store.DIGEST.fullmatch(key) for key in verified):
        raise ValueError('invalid verification checkpoint')
    refs.update(verified)
    upload = read_metadata(root / 'upload-files.txt', metadata, owner, '')
    names = upload.splitlines()
    if any(not store.DIGEST.fullmatch(name) for name in names):
        raise ValueError('invalid upload object list')
    refs.update(names)
    retiring = read_metadata(root / 'retiring.json', metadata, owner, [])
    if not isinstance(retiring, list) or any(not isinstance(v, str) or not store.GENERATION.fullmatch(v) for v in retiring):
        raise ValueError('invalid retirement journal')
    return refs, directories, metadata


def object_signature(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def validate_object(name, value, owner, device):
    if (not store.DIGEST.fullmatch(name) or not stat.S_ISREG(value.st_mode)
            or value.st_uid != owner or value.st_dev != device
            or value.st_nlink != 1 or value.st_mode & 0o222):
        raise ValueError('unsafe staging object; cleanup refused')


def check_references(plan):
    for path, expected in plan.directories:
        if directory_signature(path) != expected:
            raise RuntimeError('staging directory changed; cleanup stopped')
    if directory_signature(plan.root / 'objects')[:2] != plan.objects_identity:
        raise RuntimeError('staging objects directory changed; cleanup stopped')
    for path, expected in plan.metadata:
        try:
            actual = tuple(store.signature(path))
        except FileNotFoundError:
            actual = None
        if actual != expected:
            raise RuntimeError('staging references changed; cleanup stopped')


def plan_cleanup(root, check, *, lock_fd=9, lock_path=JOB_LOCK):
    require_lock(lock_fd, lock_path)
    check()
    refs, directories, metadata = reference_state(root)
    owner = root.lstat().st_uid
    identity = directory_signature(root / 'objects', owner)[:2]
    candidates = []
    for path in sorted((root / 'objects').iterdir()):
        value = path.lstat()
        validate_object(path.name, value, owner, identity[0])
        if metadata[root / 'capture-index.json'] is None:
            raise ValueError('capture index missing from nonempty staging store')
        if path.name not in refs:
            candidates.append(ObjectCandidate(path.name, object_signature(value), value.st_blocks * 512))
    plan = CleanupPlan(root, tuple(directories.items()), tuple(metadata.items()),
                       identity, len(refs), tuple(candidates))
    check()
    check_references(plan)
    return plan


def apply_cleanup(plan, check, *, lock_fd=9, lock_path=JOB_LOCK):
    require_lock(lock_fd, lock_path)
    if plan_cleanup(plan.root, check, lock_fd=lock_fd, lock_path=lock_path) != plan:
        raise RuntimeError('staging cleanup plan changed; inspect again')
    objects = plan.root / 'objects'
    fd = os.open(objects, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != plan.objects_identity:
            raise RuntimeError('staging objects directory changed')
        for candidate in plan.candidates:
            check()
            check_references(plan)
            value = os.stat(candidate.name, dir_fd=fd, follow_symlinks=False)
            validate_object(candidate.name, value, os.fstat(fd).st_uid, os.fstat(fd).st_dev)
            if object_signature(value) != candidate.signature:
                raise RuntimeError('staging object changed; cleanup stopped')
            os.unlink(candidate.name, dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)
    return plan.summary()
