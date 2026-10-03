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
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LIVE_ROOT = Path('/home/pi/scripts/python-packages')
FLAT_ROOT = Path('/home/pi/scripts/python-automation')
UNIT = 'van-dashboard.service'
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
            return {'release': str(release), 'restarted': []}
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
            return {'release': str(release), 'restarted': restarted}
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
        return {'release': str(release), 'restarted': [UNIT] if restarted else []}


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
