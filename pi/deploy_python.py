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
# This table also supplies the generic sync's unit exclusion list.
SERVICES = {
    'video-library.service': {
        'app': 'video_library', 'interpreter': '/usr/bin/python3',
        'flat': 'video_library_server.py',
        'extra': ('shared/__init__.py', 'shared/python/__init__.py', 'shared/python/sonos_tasks.py'),
    },
    'audiobooks.service': {
        'app': 'audiobooks', 'interpreter': '/usr/bin/python3',
        'flat': 'audiobook_server.py', 'extra': (),
    },
    'bme280-mqtt.service': {
        'app': 'bme280', 'interpreter': '/home/pi/pyvenv/bin/python',
        'flat': 'bme280_mqtt.py', 'extra': (),
    },
    'vanpi-metered.service': {
        'app': 'metered', 'interpreter': '/usr/bin/python3', 'flat': None,
        'extra_dirs': ('shared/metered/',),
        'extra': ('shared/__init__.py', 'shared/python/__init__.py', 'shared/python/tool_settings.py',
                  'pi/apps/video_library/__init__.py', 'pi/apps/video_library/video_qbittorrent.py',
                  'pi/apps/video_library/qbittorrent_availability.py'),
    },
}
# Units other repos own and deploy that import from this package release and
# publish /run/<unit stem>/package-release like SERVICES do. GC keeps their
# running release; this installer never installs, selects or restarts them.
EXTERNAL_CONSUMERS = ('van-dashboard.service',)
# Only reviewed launch forms prove the consumer no longer executes the removed
# package; a wrapper could hide that import even when its argv names no module.
CONSUMER_LAUNCH_FORMS = frozenset({
    ('/usr/bin/test',),
    ('/usr/bin/python3', '-P', '-m', 'van_dashboard'),
})
CONSUMER_EXEC_PROPERTIES = ('ExecCondition', 'ExecStartPre', 'ExecStart', 'ExecStartPost',
                            'ExecReload', 'ExecStop', 'ExecStopPost')
CONSUMER_PROPERTIES = ('LoadState', 'NeedDaemonReload', 'FragmentPath', 'DropInPaths',
                       *CONSUMER_EXEC_PROPERTIES)
RETAIN_NEWEST = 3
GC_DELETE_LIMIT = 16
# Only immediate Python modules in these reviewed directories are inputs.
MODULE_DIRS = (
    'pi/apps/audiobooks', 'pi/apps/bme280', 'pi/apps/media_organizer',
    'pi/apps/iptorrents_watch', 'pi/apps/video_library',
    'pi/apps/video_library/players', 'pi/scripts/python', 'shared/python',
    'pi/apps/metered', 'shared/metered',
)
INITIALIZERS = ('pi/__init__.py', 'pi/apps/__init__.py',
                'pi/scripts/__init__.py', 'shared/__init__.py', 'pi/package_runtime.py')
ASSETS = ('shared/metered/consumers.json',
          'pi/apps/audiobooks/templates/audiobooks.html',
          'pi/apps/video_library/templates/video_library.html',
          'pi/apps/video_library/static/video_library.js',
          'pi/apps/video_library/static/video_library.css')


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def source_files(repo):
    paths = list(INITIALIZERS) + list(ASSETS) + [f'pi/services/{unit}' for unit in SERVICES]
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


def selected_units(selected=None):
    units = list(SERVICES) if selected is None else list(dict.fromkeys(selected))
    if not units or any(unit not in SERVICES for unit in units):
        raise ValueError('unknown or empty package service selection')
    return units


def build_plan(repo=ROOT, mode='stage', selected=None):
    if mode not in ('stage', 'activate', 'update'):
        raise ValueError('legacy flatten is retired; the flat rollback tree is frozen')
    selected = selected_units(selected)
    repo = repo.resolve()
    files = {p: digest((repo / p).read_bytes()) for p in source_files(repo)}
    dependencies = {}
    for unit, spec in SERVICES.items():
        paths = {p: sha for p, sha in files.items()
                 if p.startswith((f"pi/apps/{spec['app']}/", *spec.get('extra_dirs', ()))) or
                 p in ('pi/__init__.py', 'pi/apps/__init__.py', 'pi/package_runtime.py', *spec['extra'])}
        dependencies[unit] = digest(encoded(paths))
    manifest = {'schema': 1, 'provenance': provenance(repo), 'files': files,
                'services': dependencies}
    release = digest(encoded(manifest))[:24]
    units = {unit: ('unchanged (stage only)' if mode == 'stage' else
                    'not selected; running release protected' if unit not in selected else
                    'explicit cutover: verify/save flat unit once; restart changed active service; retry pending intent'
                    if mode == 'activate' else
                    'require prior activation; compare service digest/unit; restart changed active service; retry pending intent')
             for unit in SERVICES}
    return {'mode': mode, 'release': release, 'manifest': manifest,
            'destination': str(LIVE_ROOT / 'releases' / release),
            'sources': [{'source': str(repo / p), 'relative': p,
                         'destination': str(LIVE_ROOT / 'releases' / release / p)}
                        for p in files], 'units': units}


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


def running_release(root, services, unit):
    if services is None:
        raise ValueError(f'{unit} service state unavailable')
    state = services.service_state(unit)
    if state['ActiveState'] in ('inactive', 'failed') and state['MainPID'] == '0':
        return None
    if state['ActiveState'] != 'active':
        raise ValueError(f'{unit} service is transitional or unknown')
    record = services.read_running_record(unit)
    if (not re.fullmatch('[0-9a-f]{32}', state['InvocationID']) or
            str(record['pid']) != state['MainPID'] or state['MainPID'] == '0' or
            record['invocation_id'] != state['InvocationID']):
        raise ValueError(f'{unit} running record identity mismatch')
    # The entrypoint records the already-pinned pi.__path__, not current.
    package = Path(record['package_path'])
    release = package.parent
    if (package.name != 'pi' or release.parent != root / 'releases' or
            not re.fullmatch('[0-9a-f]{24}', release.name)):
        raise ValueError(f'unexpected {unit} running release')
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
        for unit in (*SERVICES, *EXTERNAL_CONSUMERS):
            running = running_release(root, services, unit)
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
    def __init__(self, units=Path('/etc/systemd/system'), systemctl='/usr/bin/systemctl'):
        self.units = units
        self.systemctl = systemctl

    def consumer_state(self, unit):
        """What systemd will execute (`show`) plus every unit file on disk or loaded."""
        # Explicit properties retain empty scalars and exclude ExecMain* metadata.
        arguments = [item for key in CONSUMER_PROPERTIES for item in ('-p', key)]
        result = subprocess.run([self.systemctl, 'show', unit, *arguments], check=True,
                                text=True, capture_output=True, timeout=10)
        properties = parse_show(result.stdout)
        base = str(self.units / unit)
        paths = [single(properties, 'FragmentPath'), *single(properties, 'DropInPaths').split()]
        if os.path.lexists(base):
            paths.append(base)
        directory = self.units / (unit + '.d')
        if directory.is_dir():
            paths += [str(path) for path in sorted(directory.glob('*.conf'))]
        # A listed file that cannot be read raises: fail closed, never "absent".
        return properties, [(path, Path(path).read_bytes()) for path in dict.fromkeys(paths) if path]

    def read_unit(self, unit):
        path = self.units / unit
        if path.is_symlink():
            raise ValueError(f'refusing symlinked unit: {unit}')
        return path.read_bytes()

    def require_absent(self, unit):
        result = subprocess.run(['/usr/bin/systemctl', 'show', '-p', 'LoadState', '-p', 'FragmentPath',
                                 '-p', 'DropInPaths', unit], text=True, capture_output=True, timeout=10)
        values = dict(line.split('=', 1) for line in result.stdout.splitlines())
        if values != {'LoadState': 'not-found', 'FragmentPath': '', 'DropInPaths': ''}:
            raise ValueError(f'{unit}: another unit or override already exists')

    def dropins(self, unit):
        # Refuse vendor/runtime fragments or overrides we cannot save/restore
        # exactly. Do not silently deploy underneath a different effective unit.
        result = subprocess.run(
            ['/usr/bin/systemctl', 'show', '-p', 'FragmentPath', '-p', 'DropInPaths', unit],
            check=True, text=True, capture_output=True, timeout=10)
        properties = dict(line.split('=', 1) for line in result.stdout.splitlines())
        if properties.get('FragmentPath') != str(self.units / unit):
            raise ValueError(f'unexpected effective unit path: {unit}')
        if 'DropInPaths' not in properties:
            raise ValueError(f'missing effective drop-in paths: {unit}')
        directory = self.units / (unit + '.d')
        if directory.is_symlink():
            raise ValueError(f'symlinked drop-in directory: {unit}')
        effective = properties['DropInPaths'].split()
        local = sorted(directory.glob('*.conf')) if directory.exists() else []
        if set(effective) != {str(path) for path in local}:
            raise ValueError(f'nonlocal or stale drop-ins require operator review: {unit}')
        if any(path.is_symlink() or not path.is_file() for path in local):
            raise ValueError(f'unsafe drop-in: {unit}')
        return {path.name: path.read_bytes() for path in local}

    def save_legacy(self, unit, destination, dropins):
        if destination.exists():
            if not (destination / unit).is_file():
                raise ValueError(f'incomplete original-unit backup: {destination}')
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.unit-backup-', dir=destination.parent) as name:
            temporary = Path(name)
            (temporary / unit).write_bytes(self.read_unit(unit))
            if dropins:
                directory = temporary / (unit + '.d')
                directory.mkdir()
                for filename, data in dropins.items():
                    (directory / filename).write_bytes(data)
            os.replace(temporary, destination)
            temporary.mkdir()

    def install_unit(self, unit, source):
        subprocess.run(['sudo', '/usr/bin/install', '-m', '0644', str(source),
                        str(self.units / unit)], check=True)
        subprocess.run(['sudo', '/usr/bin/systemctl', 'daemon-reload'], check=True)

    def is_active(self, unit):
        result = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet', unit])
        if result.returncode not in (0, 3, 4):
            raise RuntimeError(f'cannot determine service state: {unit}')
        return result.returncode == 0

    def service_state(self, unit):
        result = subprocess.run(
            ['/usr/bin/systemctl', 'show', '-p', 'ActiveState', '-p', 'MainPID',
             '-p', 'InvocationID', unit], check=True, text=True, capture_output=True,
            timeout=10)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines())
        if set(state) != {'ActiveState', 'MainPID', 'InvocationID'}:
            raise ValueError(f'incomplete service state: {unit}')
        return state

    def read_running_record(self, unit):
        record = Path('/run') / unit.removesuffix('.service') / 'package-release'
        if record.resolve(strict=True) != record:
            raise ValueError(f'symlinked running record: {unit}')
        return json.loads(read_gc_file(record, record.parent.stat().st_dev))

    def restart(self, unit):
        subprocess.run(['sudo', '/usr/bin/systemctl', 'restart', unit], check=True)


def directives(raw):
    """Recognize simple systemd launch directives; refuse ambiguous continuations."""
    text = raw.decode()
    if '\\\n' in text or '\r' in text:
        raise ValueError('unit continuations require operator review')
    section = None
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('['):
            section = line
        elif section == '[Service]' and '=' in line:
            entries.append(tuple(part.strip() for part in line.split('=', 1)))
    return entries


def recognized_unit(raw, unit, package):
    spec = SERVICES[unit]
    if raw is None or (not package and spec['flat'] is None):
        return False
    expected = (f"{spec['interpreter']} -P -m pi.apps.{spec['app']}" if package else
                f"{spec['interpreter']} /home/pi/scripts/python-automation/{spec['flat']}")
    return [value for key, value in directives(raw) if key == 'ExecStart'] == [expected]


def validate_dropins(unit, dropins):
    import shlex
    for name, raw in dropins.items():
        for key, value in directives(raw):
            if (key in ('ExecStart', 'ExecStartPre', 'ExecStopPost', 'EnvironmentFile',
                        'UnsetEnvironment', 'PassEnvironment', 'RootDirectory', 'RootImage', 'User') or
                    key.startswith('RuntimeDirectory') or
                    (key == 'Environment' and (not value or any(
                        item.split('=', 1)[0] in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONDONTWRITEBYTECODE')
                        for item in shlex.split(value))))):
                raise ValueError(f'{unit} drop-in {name} overrides package launch: {key}')


def parse_show(text):
    """`systemctl show` output as {property: [values]}; Exec* properties repeat."""
    properties = {}
    for line in text.splitlines():
        if not line:
            continue
        key, separator, value = line.partition('=')
        if not separator:
            raise ValueError(f'unparseable systemctl show line: {line!r}')
        properties.setdefault(key, []).append(value)
    return properties


def single(properties, key):
    if len(properties.get(key, ())) != 1:
        raise ValueError(f'systemctl show gave no single {key}')
    return properties[key][0]


def consumer_exec_command(value):
    """Accept a complete systemd command record, not an argv substring in debris."""
    import shlex
    record = re.fullmatch(
        r'\{ path=(/\S+) ; argv\[\]=((?:(?! ; ignore_errors=).)*) ; ignore_errors=(?:yes|no) ; '
        r'start_time=\[[^\]\r\n]*\] ; stop_time=\[[^\]\r\n]*\] ; '
        r'pid=[0-9]+ ; code=[^;\s]+ ; status=[^;\s]+ \}', value)
    if not record:
        raise ValueError(f'unparseable Exec record: {value!r}')
    words = shlex.split(record[2])
    if not words or words[0] != record[1]:
        raise ValueError(f'Exec path and argv disagree: {value!r}')
    return record[2]


def allowed_consumer_command(command, unit_file=False):
    import shlex
    words = shlex.split(command)
    if unit_file and words:
        words[0] = words[0].lstrip('-@:+!')
    for form in CONSUMER_LAUNCH_FORMS:
        if tuple(words[:len(form)]) == form:
            return True
        if (len(form) > 1 and len(words) >= len(form)
                and tuple(words[:len(form) - 1]) == form[:-1]
                and words[len(form) - 1].startswith(form[-1] + '.')):
            return True
    return False


def consumer_problem(properties, files):
    """Why an external consumer unit must block a release, or None. Raises on anything unreadable."""
    load, reload = single(properties, 'LoadState'), single(properties, 'NeedDaemonReload')
    fragment, dropins = single(properties, 'FragmentPath'), single(properties, 'DropInPaths')
    if reload != 'no':
        return 'has unit changes pending a daemon-reload, so systemd still runs its old commands'
    if load == 'loaded' and not fragment:
        return 'is loaded without a unit file that can be checked'
    if load not in ('loaded', 'not-found'):
        return f'is in load state {load}'
    if properties.keys() - set(CONSUMER_PROPERTIES):
        raise ValueError('unexpected systemctl show properties')
    commands = [(consumer_exec_command(value), False)
                for key in CONSUMER_EXEC_PROPERTIES for value in properties.get(key, ()) if value]
    if load == 'not-found' and (fragment or dropins or commands or files):
        return 'is not found but still has a unit file, drop-in or command'
    for path, raw in files:
        commands += [(value, True) for key, value in directives(raw) if key.startswith('Exec') and value]
    for command, unit_file in commands:
        if not allowed_consumer_command(command, unit_file):
            return (f'has unsupported launch {command!r}; update the guard\'s allowlist '
                    'if the unit legitimately changed')
    return None


def check_external_consumers(services):
    """Refuse a release whose current link would strand another repo's unit.

    Those units import through current, so a release missing a module their
    effective commands run (the scripts-era dashboard unit's
    -m pi.apps.van_dashboard) would fail at their next restart, relay-off
    included. Reads systemd's effective state, not just the unit file, and
    fails closed on any read or parse error.
    """
    for unit in EXTERNAL_CONSUMERS:
        try:
            problem = consumer_problem(*services.consumer_state(unit))
        except Exception as error:
            raise ValueError(f'{unit}: cannot verify its effective launch commands '
                             f'({type(error).__name__}: {error}); deploy the van-dashboard repo '
                             'first (its docs/cutover.md). No state, link or unit was changed.') from error
        if problem:
            raise ValueError(f'{unit} {problem}; deploy the van-dashboard repo first (its '
                             'docs/cutover.md), then daemon-reload and retry. '
                             'No state, link or unit was changed.')


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.state-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(encoded(data))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_state(path):
    if path.is_symlink():
        raise ValueError(f'symlinked service state: {path}')
    if not path.exists():
        return None
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or
            not re.fullmatch('[0-9a-f]{24}', value.get('release', '')) or
            not re.fullmatch('[0-9a-f]{64}', value.get('digest', ''))):
        raise ValueError(f'invalid service state: {path}')
    return value


def unit_preflight(services, unit, mode, saved):
    try:
        live = services.read_unit(unit)
    except FileNotFoundError:
        if mode != 'activate' or saved is not None or SERVICES[unit]['flat'] is not None:
            raise
        services.require_absent(unit)
        return None, False, False, {}
    dropins = services.dropins(unit)
    validate_dropins(unit, dropins)
    return live, recognized_unit(live, unit, True), recognized_unit(live, unit, False), dropins


def install_release(stream, root, mode, services=None, selected=None):
    """Install under a lock; per-service intent survives link switches and failures."""
    import fcntl
    if mode not in ('stage', 'activate', 'update'):
        raise ValueError('legacy flatten is retired; the flat rollback tree is frozen')
    selected = selected_units(selected)
    if root.is_symlink() or (root / 'releases').is_symlink():
        raise ValueError('release root must not be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        def completed(release, restarted):
            return {'release': str(release), 'restarted': restarted,
                    'gc': collect_releases(root, lock, services, release)}

        old = current_release(root)
        if mode == 'update' and old is None:
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
        if services is None:
            raise ValueError('activation requires a service manager')
        check_external_consumers(services)
        work = []
        # Validate ALL selected services before saving, journaling or changing
        # current/units. --update cannot silently undo a deliberate flat rollback.
        for unit in selected:
            state_path = root / 'service-state' / (unit + '.json')
            pending_path = root / 'pending-restarts' / (unit + '.json')
            saved = read_state(state_path)
            pending = read_state(pending_path)
            if pending is not None and not isinstance(pending.get('restart'), bool):
                raise ValueError(f'invalid pending restart intent: {unit}')
            live, packaged, flat, dropins = unit_preflight(services, unit, mode, saved)
            backup = root / 'pre-package-units' / (unit + '.backup')
            if mode == 'update' and (saved is None or not packaged):
                raise ValueError(f'{unit}: explicit --activate --service {unit} required')
            if live is not None and not flat and not packaged:
                raise ValueError(f'{unit}: expected recognizable flat or package ExecStart')
            resumable = pending is not None and (backup.exists() or SERVICES[unit]['flat'] is None)
            if saved is None and packaged and not resumable:
                raise ValueError(f'{unit}: first activation requires the expected flat unit')
            new = (release / f'pi/services/{unit}').read_bytes()
            if not recognized_unit(new, unit, package=True):
                raise ValueError(f'{unit}: invalid packaged source unit')
            changed = (pending is not None or live != new or saved is None or
                       saved['digest'] != manifest['services'][unit])
            restart = changed and (services.is_active(unit) or
                                   (pending is not None and pending['restart']))
            work.append({'unit': unit, 'state': state_path, 'pending': pending_path,
                         'backup': backup, 'save': flat,
                         'dropins': dropins, 'changed': changed, 'restart': restart,
                         'install': live != new or pending is not None})
        for item in work:
            unit = item['unit']
            if item['save']:
                services.save_legacy(unit, item['backup'], item['dropins'])
            if item['changed']:
                atomic_json(item['pending'], {'release': release_id,
                            'digest': manifest['services'][unit], 'restart': item['restart']})
        if old != release:
            if old:
                replace_link(root, 'previous', 'releases/' + old.name)
            replace_link(root, 'current', 'releases/' + release_id)
        restarted = []
        for item in work:
            unit = item['unit']
            if item['install']:
                # Retry reload even when a previous install copied matching bytes.
                services.install_unit(unit, release / f'pi/services/{unit}')
            if item['restart']:
                services.restart(unit)
                restarted.append(unit)
            atomic_json(item['state'], {'release': release_id, 'digest': manifest['services'][unit]})
            # The host activation gate pi/sync_scripts.sh checks before any upload.
            atomic_json(root / 'activated.json', {'release': release_id})
            item['pending'].unlink(missing_ok=True)
        return completed(release, restarted)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='print a local JSON plan; never connect')
    parser.add_argument('--list-units', action='store_true', help='print package-owned units locally; never connect')
    parser.add_argument('--check-consumers', action='store_true',
                        help="read-only: require external consumers' reviewed post-cutover launch forms")
    parser.add_argument('--target', default='pi@vanpi.lan')
    parser.add_argument('--service', action='append', choices=tuple(SERVICES), help='select service(s); default all')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--activate', action='store_true', help='explicit per-service first cutover (saves old units)')
    group.add_argument('--update', action='store_true', help='update only already activated services')
    group.add_argument('--legacy-flatten', action='store_true', help='retired: always refuses; flat tree is frozen')
    parser.add_argument('--receive', choices=('stage', 'activate', 'update', 'consumers'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.legacy_flatten:
        parser.error('--legacy-flatten is retired; the flat rollback tree is frozen')
    if args.list_units:
        if args.receive or args.activate or args.update or args.service or args.dry_run:
            parser.error('--list-units must be used alone')
        print('\n'.join(SERVICES))
        return
    if args.receive == 'consumers':
        try:
            check_external_consumers(SystemServices())
        except ValueError as error:
            parser.exit(1, f'{error}\n')
        return
    if args.receive:
        if args.dry_run:
            parser.error('--receive cannot be combined with --dry-run')
        if sys.version_info < (3, 11):
            parser.error('Python 3.11 or newer is required')
        print(json.dumps(install_release(sys.stdin.buffer, LIVE_ROOT, args.receive,
                                         SystemServices(), selected=args.service)))
        return
    if args.check_consumers:
        if args.receive or args.activate or args.update or args.service or args.dry_run:
            parser.error('--check-consumers must be used alone (except --target)')
    mode = 'consumers' if args.check_consumers else (
        'activate' if args.activate else 'update' if args.update else 'stage')
    if not args.check_consumers:
        plan = build_plan(mode=mode, selected=args.service)
        print(json.dumps(plan, indent=2, sort_keys=True), flush=True)
        if args.dry_run:
            return
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+', args.target) or args.target.startswith('-'):
        parser.error('invalid SSH target')
    import shlex
    receiver = Path(__file__).read_text()
    receiver = receiver.replace('ROOT = Path(__file__).resolve().parents[1]', 'ROOT = Path.cwd()')
    command = '/usr/bin/python3 -c ' + shlex.quote(receiver) + ' --receive ' + mode
    if args.check_consumers:
        # Same receiver-side check the install runs; sync_scripts.sh calls this before any upload.
        raise SystemExit(subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                                         '--', args.target, command], stdin=subprocess.DEVNULL).returncode)
    for unit in args.service or ():
        command += ' --service ' + shlex.quote(unit)
    with tempfile.TemporaryFile() as archive:
        make_archive(plan, ROOT, archive)
        archive.seek(0)
        subprocess.run(['ssh', '-o', 'BatchMode=yes', '--', args.target, command],
                       stdin=archive, check=True)


if __name__ == '__main__':
    main()
