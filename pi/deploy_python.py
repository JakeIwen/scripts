#!/usr/bin/env python3
"""Allowlisted Pi Python releases. Dry-run is entirely local; cutover is opt-in."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LIVE_ROOT = Path('/home/pi/scripts/python-packages')
FLAT_ROOT = Path('/home/pi/scripts/python-automation')
UNIT = 'van-dashboard.service'
RETAIN_NEWEST = 3
GC_DELETE_LIMIT = 16
RUNNING_RECORD = Path('/run/van-dashboard/package-release')
# Only immediate Python modules in these reviewed directories are inputs.
MODULE_DIRS = (
    'pi/apps/audiobooks', 'pi/apps/bme280', 'pi/apps/van_dashboard',
    'pi/apps/van_dashboard/routes', 'pi/apps/video_library',
    'pi/apps/video_library/players', 'pi/scripts/python', 'shared/python',
)
INITIALIZERS = ('pi/__init__.py', 'pi/apps/__init__.py',
                'pi/scripts/__init__.py', 'shared/__init__.py')
ASSETS = ('pi/apps/video_library/templates/video_library.html',
          'pi/apps/video_library/static/video_library.js',
          'pi/apps/video_library/static/video_library.css')
LEGACY_DIRS = tuple(p for p in MODULE_DIRS if not p.startswith('pi/apps/van_dashboard'))


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_files(repo):
    paths = list(INITIALIZERS) + list(ASSETS) + [f'pi/services/{UNIT}']
    for directory in MODULE_DIRS:
        found = sorted((repo / directory).glob('*.py'))
        if not found:
            raise ValueError(f'empty module directory: {repo / directory}')
        paths.extend(p.relative_to(repo).as_posix() for p in found)
    for relative in paths:
        path = repo / relative
        if (any(parent.is_symlink() for parent in (path, *path.parents) if parent != repo and parent.is_relative_to(repo))
                or not path.is_file() or not path.resolve().is_relative_to(repo)):
            raise ValueError(f'missing or unsafe source: {path}')
    return sorted(set(paths))


def provenance(repo):
    def git(*args):
        result = subprocess.run(['git', '-C', str(repo), *args],
                                text=True, capture_output=True, check=True)
        return result.stdout.strip()
    return {'checkout': str(repo), 'commit': git('rev-parse', 'HEAD'),
            'branch': git('rev-parse', '--abbrev-ref', 'HEAD'),
            'dirty': bool(git('status', '--porcelain'))}


def legacy_destination(relative):
    path = PurePosixPath(relative)
    if relative in ASSETS:
        return '/'.join(path.parts[-2:])
    if str(path.parent) in LEGACY_DIRS and path.name != '__init__.py':
        return path.name
    return None


def build_plan(repo=ROOT, mode='stage'):
    repo = repo.resolve()
    files = {p: digest((repo / p).read_bytes()) for p in source_files(repo)}
    legacy = {p: legacy_destination(p) for p in files if legacy_destination(p)}
    if len(set(legacy.values())) != len(legacy):
        raise ValueError('duplicate legacy destination (basename collision)')
    dependencies = {p: sha for p, sha in files.items()
                    if (p.startswith('pi/apps/van_dashboard/') and
                        not p.endswith('/react_dashboard_preview.py')) or
                    p in ('pi/__init__.py', 'pi/apps/__init__.py')}
    manifest = {'schema': 1, 'provenance': provenance(repo), 'files': files,
                'services': {UNIT: digest(encoded(dependencies))}, 'legacy': legacy}
    release = digest(encoded(manifest))[:24]
    units = {UNIT: ('unchanged (stage only)' if mode in ('stage', 'legacy') else
                   'compare live dependency digest and unit; restart only if changed')}
    if mode == 'legacy':
        units.update({u: 'restart changed active service, or complete a pending failed restart'
                      for u in ('audiobooks.service', 'bme280-mqtt.service', 'video-library.service')})
    return {'mode': mode, 'release': release, 'manifest': manifest,
            'destination': str(LIVE_ROOT / 'releases' / release),
            'sources': [{'source': str(repo / p), 'relative': p,
                         'destination': str(LIVE_ROOT / 'releases' / release / p)}
                        for p in files], 'units': units,
            'legacy_destinations': {str(repo / p): str(FLAT_ROOT / dest)
                                    for p, dest in legacy.items()} if mode == 'legacy' else {}}


def make_archive(plan, repo, output):
    with tarfile.open(fileobj=output, mode='w') as archive:
        raw = encoded(plan['manifest'])
        member = tarfile.TarInfo('manifest.json')
        member.size = len(raw)
        member.mode = 0o644
        archive.addfile(member, io.BytesIO(raw))
        for relative, expected in plan['manifest']['files'].items():
            raw = (repo / relative).read_bytes()
            if digest(raw) != expected:
                raise ValueError(f'source changed while staging: {relative}')
            member = tarfile.TarInfo(relative)
            member.size = len(raw)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(raw))


def unpack_archive(stream, incoming):
    """Read regular files only, validate every path/hash before publishing."""
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        first = archive.next()
        if first is None or first.name != 'manifest.json' or not first.isfile():
            raise ValueError('archive must begin with manifest.json')
        raw = archive.extractfile(first).read()
        manifest = json.loads(raw)
        if manifest.get('schema') != 1:
            raise ValueError('unsupported release schema')
        expected = manifest['files']
        seen = set()
        for member in archive:
            if member is first:
                continue
            path = PurePosixPath(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts or
                    member.name not in expected or member.name in seen):
                raise ValueError(f'unsafe or unexpected archive member: {member.name}')
            data = archive.extractfile(member).read()
            if digest(data) != expected[member.name]:
                raise ValueError(f'checksum mismatch: {member.name}')
            if member.name.endswith('.py'):
                compile(data, member.name, 'exec')
            destination = incoming / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o644)
            seen.add(member.name)
        if seen != set(expected):
            raise ValueError('incomplete release archive')
        (incoming / 'manifest.json').write_bytes(encoded(manifest))
        return manifest


def replace_link(root, name, target):
    link = root / name
    if link.exists() and not link.is_symlink():
        raise ValueError(f'refusing non-symlink: {link}')
    temporary = root / f'.{name}.{os.getpid()}'
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def current_release(root):
    link = root / 'current'
    if not link.is_symlink():
        if link.exists():
            raise ValueError('current is not a symlink')
        return None
    target = os.readlink(link)
    if not re.fullmatch(r'releases/[0-9a-f]{24}', target):
        raise ValueError('unexpected current release target')
    release = root / target
    if release.is_symlink() or (root / 'releases').is_symlink():
        raise ValueError('release paths must not be symlinks')
    if not release.is_dir():
        raise ValueError('current release is missing')
    return release


def mount_points():
    """Linux mountinfo includes same-device bind mounts (st_dev alone cannot)."""
    points = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        if len(fields) < 10 or '-' not in fields[6:]:
            raise ValueError('unrecognized mountinfo')
        point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4])
        if not point.startswith('/'):
            raise ValueError('nonabsolute mountpoint')
        points.append(Path(point))
    if not points:
        raise ValueError('empty mountinfo')
    return points


def check_gc_lock(root, lock):
    """Verify the receiver's open lock still names the locked, owned inode."""
    import fcntl
    info = (root / '.install.lock').lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
            info.st_mode & 0o022 or info.st_nlink != 1 or
            not os.path.samestat(info, os.fstat(lock.fileno()))):
        raise ValueError('unsafe or replaced install lock')
    # A separate open description must conflict with the receiver's flock.
    descriptor = os.open(root / '.install.lock', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as probe:
        if not os.path.samestat(info, os.fstat(probe.fileno())):
            raise ValueError('install lock replaced during cleanup')
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Also reject a descriptor that belongs to a different lock owner.
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise ValueError('install lock is not held')


def check_gc_node(path, device, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_dev != device or
            info.st_uid != os.geteuid() or info.st_mode & 0o022 or
            (not directory and info.st_nlink != 1)):
        raise ValueError(f'unsafe release node: {path}')
    return info


def read_gc_file(path, device):
    info = check_gc_node(path, device)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as handle:
        if not os.path.samestat(info, os.fstat(handle.fileno())):
            raise ValueError(f'file replaced during cleanup: {path}')
        return handle.read()


def check_release_tree(release, device):
    """Recognize receiver-owned trees, without following any links."""
    info = check_gc_node(release, device, directory=True)
    raw = read_gc_file(release / 'manifest.json', device)
    manifest = json.loads(raw)
    if (manifest.get('schema') != 1 or digest(encoded(manifest))[:24] != release.name
            or not isinstance(manifest.get('files'), dict) or not manifest['files']):
        raise ValueError(f'unrecognized release manifest: {release.name}')
    files = {'manifest.json'}
    directories = set()
    for name, sha in manifest['files'].items():
        path = PurePosixPath(name)
        if (path.is_absolute() or '..' in path.parts or str(path) != name or
                name in ('.', 'manifest.json') or not re.fullmatch('[0-9a-f]{64}', sha)):
            raise ValueError(f'unsafe release manifest: {release.name}')
        files.add(name)
        directories.update(str(parent) for parent in path.parents if str(parent) != '.')
    seen_files, seen_dirs = set(), set()

    def visit(directory):
        for child in directory.iterdir():
            relative = child.relative_to(release).as_posix()
            is_directory = stat.S_ISDIR(child.lstat().st_mode)
            check_gc_node(child, device, directory=is_directory)
            if is_directory:
                if relative not in directories:
                    raise ValueError(f'unexpected release directory: {child}')
                seen_dirs.add(relative)
                visit(child)
            else:
                if relative not in files:
                    raise ValueError(f'unexpected release file: {child}')
                seen_files.add(relative)
    visit(release)
    if seen_files != files or seen_dirs != directories:
        raise ValueError(f'incomplete release tree: {release.name}')
    return info


def dashboard_release(root, services):
    if services is None:
        raise ValueError('dashboard service state unavailable')
    state = services.dashboard_state()
    if state['ActiveState'] in ('inactive', 'failed') and state['MainPID'] == '0':
        return None
    if state['ActiveState'] != 'active':
        raise ValueError('dashboard service is transitional or unknown')
    record = services.read_running_record()
    if (not re.fullmatch('[0-9a-f]{32}', state['InvocationID']) or
            str(record['pid']) != state['MainPID'] or state['MainPID'] == '0' or
            record['invocation_id'] != state['InvocationID']):
        raise ValueError('dashboard running record identity mismatch')
    # The entrypoint records the already-pinned pi.__path__, not current.
    package = Path(record['package_path'])
    release = package.parent
    if (package.name != 'pi' or release.parent != root / 'releases' or
            not re.fullmatch('[0-9a-f]{24}', release.name)):
        raise ValueError('unexpected dashboard running release')
    return release.name


def collect_releases(root, lock, services, installed):
    """Best effort only: ambiguity retains everything, never fails an install.

    Installers are the only writers of release trees/links. A service restarting
    after the state snapshot can only import current (also protected), because
    this caller keeps the installer lock through collection.
    """
    result = {'status': 'skipped', 'kept': [], 'removed': [], 'reason': None}
    entries = []
    try:
        check_gc_lock(root, lock)
        if root.resolve(strict=True) != root:
            raise ValueError('release root has symlinked ancestors')
        device = root.lstat().st_dev
        check_gc_node(root, device, directory=True)
        releases = root / 'releases'
        check_gc_node(releases, device, directory=True)
        entries = sorted(releases.iterdir())
        # Check the entire namespace before deleting anything, including trees
        # that will be kept. Unknown debris is an operator decision, not ours.
        for path in entries:
            if not re.fullmatch('[0-9a-f]{24}', path.name):
                raise ValueError(f'unexpected release name: {path.name}')
        def check_mounts():
            if any(point == releases or point.is_relative_to(releases) for point in mount_points()):
                raise ValueError('mount at or beneath releases')
        check_mounts()
        snapshots = {path.name: check_release_tree(path, device) for path in entries}
        keep = {installed.name}
        for name in ('current', 'previous'):
            link = root / name
            try:
                info = link.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISLNK(info.st_mode):
                raise ValueError(f'{name} is not a symlink')
            target = os.readlink(link)
            if not re.fullmatch(r'releases/[0-9a-f]{24}', target):
                raise ValueError(f'unexpected {name} release target')
            keep.add(Path(target).name)
        running = dashboard_release(root, services)
        if running:
            keep.add(running)
        if not keep <= snapshots.keys():
            raise ValueError('protected release is missing')
        newest = sorted(snapshots, key=lambda name: (snapshots[name].st_mtime_ns, name), reverse=True)
        keep.update(newest[:RETAIN_NEWEST])
        if not shutil.rmtree.avoids_symlink_attacks:
            raise ValueError('fd-safe directory removal unavailable')
        retired = [name for name in reversed(newest) if name not in keep]
        for name in retired[:GC_DELETE_LIMIT]:
            path = releases / name
            # Revalidate immediately before recursion, including bind mounts.
            check_gc_lock(root, lock)
            if path.resolve(strict=True) != path:
                raise ValueError(f'release path changed during cleanup: {name}')
            check_mounts()
            info = check_release_tree(path, device)
            if not os.path.samestat(info, snapshots[name]):
                raise ValueError(f'release replaced during cleanup: {name}')
            shutil.rmtree(path)
            result['removed'].append(name)
        result['status'] = 'ok'
        result['deferred'] = retired[GC_DELETE_LIMIT:]
    except Exception as error:
        # Even failed validation or a partial removal must not undo deployment.
        result['reason'] = f'{type(error).__name__}: {error}'
    result['kept'] = [path.name for path in entries if path.name not in result['removed']]
    return result


class SystemServices:
    """Only instantiated on the Pi receiver; tests supply a fake service manager."""
    def __init__(self, units=Path('/etc/systemd/system')):
        self.units = units

    def read_unit(self, unit):
        return (self.units / unit).read_bytes()

    def save_legacy(self, destination):
        if destination.exists():
            return
        temporary = destination.with_name(destination.name + '.incoming')
        temporary.mkdir(mode=0o700)
        try:
            shutil.copy2(self.units / UNIT, temporary / UNIT)
            dropins = self.units / (UNIT + '.d')
            if dropins.exists():
                shutil.copytree(dropins, temporary / dropins.name)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def install_unit(self, unit, source):
        subprocess.run(['sudo', '/usr/bin/install', '-m', '0644', str(source),
                        str(self.units / unit)], check=True)
        subprocess.run(['sudo', '/usr/bin/systemctl', 'daemon-reload'], check=True)

    def is_active(self, unit):
        result = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', unit])
        if result.returncode not in (0, 3, 4):
            raise RuntimeError(f'cannot determine service state: {unit}')
        return result.returncode == 0

    def dashboard_state(self):
        result = subprocess.run(
            ['/usr/bin/systemctl', 'show', '-p', 'ActiveState', '-p', 'MainPID',
             '-p', 'InvocationID', UNIT], check=True, text=True, capture_output=True,
            timeout=10)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines())
        if set(state) != {'ActiveState', 'MainPID', 'InvocationID'}:
            raise ValueError('incomplete dashboard service state')
        return state

    def read_running_record(self):
        if RUNNING_RECORD.resolve(strict=True) != RUNNING_RECORD:
            raise ValueError('symlinked dashboard running record')
        return json.loads(read_gc_file(RUNNING_RECORD, RUNNING_RECORD.parent.stat().st_dev))

    def restart(self, unit):
        subprocess.run(['sudo', '/usr/bin/systemctl', 'restart', unit], check=True)


def install_release(stream, root, mode, services=None, flat_root=FLAT_ROOT):
    """Install under a lock; injectable paths/services enable offline behavioral tests."""
    import fcntl
    if root.is_symlink() or (root / 'releases').is_symlink():
        raise ValueError('release root must not be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        def completed(release, restarted):
            return {'release': str(release), 'restarted': restarted,
                    'gc': collect_releases(root, lock, services, release)}

        old = current_release(root)
        if mode == 'update' and (old is None or not (root / 'activated.json').is_file()):
            raise ValueError('initial cutover requires --activate; routine deployment refused')
        releases = root / 'releases'
        releases.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.incoming-', dir=releases) as directory:
            incoming = Path(directory)
            manifest = unpack_archive(stream, incoming)
            release_id = digest(encoded(manifest))[:24]
            release = releases / release_id
            if release.is_symlink():
                raise ValueError('release directory must not be a symlink')
            if release.exists():
                if (release / 'manifest.json').read_bytes() != encoded(manifest):
                    raise ValueError('release manifest collision')
                for p, expected in manifest['files'].items():
                    if (release / p).is_symlink() or digest((release / p).read_bytes()) != expected:
                        raise ValueError(f'existing release was modified: {p}')
            else:
                os.replace(incoming, release)
                incoming.mkdir()
        print(f'RELEASE {release}')
        if mode == 'stage':
            return completed(release, [])
        if mode == 'legacy':
            # Never include dashboard modules: they no longer support flat execution.
            mapping = {p: legacy_destination(p) for p in manifest['files'] if legacy_destination(p)}
            if len(set(mapping.values())) != len(mapping):
                raise ValueError('duplicate legacy destination')
            changed_legacy = set()
            for relative, destination in mapping.items():
                path = flat_root / destination
                if any(parent.is_symlink() for parent in (path, *path.parents)
                       if parent.is_relative_to(flat_root)):
                    raise ValueError(f'refusing legacy symlink: {path}')
                if not path.exists() or digest(path.read_bytes()) != manifest['files'][relative]:
                    changed_legacy.add(relative)
            affected = set()
            for relative in changed_legacy:
                if relative.startswith('pi/apps/video_library/') or relative == 'shared/python/sonos_tasks.py':
                    affected.add('video-library.service')
                if relative.startswith('pi/apps/audiobooks/'):
                    affected.add('audiobooks.service')
                if relative == 'pi/apps/bme280/bme280_mqtt.py':
                    affected.add('bme280-mqtt.service')
            journal = root / 'legacy-pending-restarts.json'
            pending_units = set()
            if journal.exists():
                saved = json.loads(journal.read_bytes())
                allowed = {'video-library.service', 'audiobooks.service', 'bme280-mqtt.service'}
                if not isinstance(saved, list) or any(not isinstance(unit, str) or unit not in allowed for unit in saved):
                    raise ValueError('invalid legacy restart journal')
                pending_units.update(saved)
            if services is not None:
                pending_units.update(unit for unit in affected if services.is_active(unit))
            if pending_units and services is None:
                raise ValueError('pending legacy restarts require a service manager')

            def save_pending():
                descriptor, name = tempfile.mkstemp(prefix='.legacy-restarts-', dir=root)
                try:
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(encoded(sorted(pending_units)))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(name, journal)
                finally:
                    Path(name).unlink(missing_ok=True)

            # Journal BEFORE copying: a partial copy or failed restart must not
            # become an apparent no-op when the same release is retried.
            if pending_units:
                save_pending()
            for relative in sorted(changed_legacy):
                path = flat_root / mapping[relative]
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(release / relative, path)
            restarted = []
            for unit in sorted(pending_units):
                services.restart(unit)
                restarted.append(unit)
                pending_units.remove(unit)
                save_pending()
            journal.unlink(missing_ok=True)
            return completed(release, restarted)
        if services is None:
            raise ValueError('activation requires a service manager')
        live_unit = services.read_unit(UNIT)
        new_unit = (release / f'pi/services/{UNIT}').read_bytes()
        old_manifest = json.loads((old / 'manifest.json').read_bytes()) if old else {}
        pending = root / 'pending-restart'
        retry_pending = pending.exists()
        changed = (retry_pending or live_unit != new_unit or
                   old_manifest.get('services', {}).get(UNIT) != manifest['services'][UNIT])
        if not (root / 'activated.json').exists() and not (root / 'pre-package-units').exists():
            if b'/home/pi/scripts/python-automation/van_dashboard.py' not in live_unit:
                raise ValueError('first activation requires an existing flat dashboard unit')
            services.save_legacy(root / 'pre-package-units')
        if changed:
            pending.write_text(release_id + '\n')
        if old != release:
            if old:
                replace_link(root, 'previous', 'releases/' + old.name)
            replace_link(root, 'current', 'releases/' + release_id)
        # A restart failure is explicit, not silently rolled back across safety hooks.
        if live_unit != new_unit or retry_pending:
            # A previous daemon-reload may have failed after the unit copy.
            services.install_unit(UNIT, release / f'pi/services/{UNIT}')
        restarted = changed and (mode == 'activate' or retry_pending or services.is_active(UNIT))
        if restarted:
            services.restart(UNIT)
        (root / 'activated.json').write_bytes(encoded({'release': release_id}))
        pending.unlink(missing_ok=True)
        return completed(release, [UNIT] if restarted else [])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='print a local JSON plan; never connect')
    parser.add_argument('--target', default='pi@vanpi.lan')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--activate', action='store_true', help='explicit first cutover (saves old units)')
    group.add_argument('--update', action='store_true', help='update only an already activated host')
    group.add_argument('--legacy-flatten', action='store_true', help='only flat-safe apps/utilities; never dashboard')
    parser.add_argument('--receive', choices=('stage', 'activate', 'update', 'legacy'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.receive:
        if args.dry_run:
            parser.error('--receive cannot be combined with --dry-run')
        if sys.version_info < (3, 11):
            parser.error('Python 3.11 or newer is required')
        print(json.dumps(install_release(sys.stdin.buffer, LIVE_ROOT, args.receive, SystemServices())))
        return
    mode = 'activate' if args.activate else 'update' if args.update else 'legacy' if args.legacy_flatten else 'stage'
    plan = build_plan(mode=mode)
    print(json.dumps(plan, indent=2, sort_keys=True), flush=True)
    if args.dry_run:
        return
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+', args.target) or args.target.startswith('-'):
        parser.error('invalid SSH target')
    import shlex
    receiver = Path(__file__).read_text()
    # -c has no __file__; only receiver mode uses this harmless replacement root.
    receiver = receiver.replace('ROOT = Path(__file__).resolve().parents[1]', 'ROOT = Path.cwd()')
    command = '/usr/bin/python3 -c ' + shlex.quote(receiver) + ' --receive ' + mode
    with tempfile.TemporaryFile() as archive:
        make_archive(plan, ROOT, archive)
        archive.seek(0)
        subprocess.run(['ssh', '-o', 'BatchMode=yes', '--', args.target, command],
                       stdin=archive, check=True)


if __name__ == '__main__':
    main()
