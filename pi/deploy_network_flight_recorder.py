#!/usr/bin/env python3
"""Checked, targeted deployment and manifest-specific rollback; never sync a repo.

check --plan /tmp/van-network-plan.json
apply --plan /tmp/van-network-plan.json
rollback --release RELEASE_ID

Check is read-only on the Pi. Apply rechecks all fingerprints before changing
files. A rollback restores exact saved files, release links and service states,
and never deletes or replaces the recorder database. No Git index operations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = Path('/var/lib/vanpi-network-deploy/releases')
FRONTEND = Path('/home/pi/scripts/van-dashboard-preview')
STORAGE_CONFIG = Path('/etc/vanpi-network-storage.json')
PACKAGE_ACTIVATION = Path('/home/pi/scripts/python-packages/activated.json')
SERVICES = ('network-flight-recorder', 'van-dashboard', 'rsyslog', 'van-dashboard-preview')
TARGETS = {
    'pi/scripts/network_flight_recorder.py': '/home/pi/scripts/network_flight_recorder.py',
    'pi/apps/van_dashboard/van_dashboard_history.py': '/home/pi/scripts/python-automation/van_dashboard_history.py',
    'pi/apps/van_dashboard/van_dashboard.py': '/home/pi/scripts/python-automation/van_dashboard.py',
    'pi/services/network-flight-recorder.service': '/etc/systemd/system/network-flight-recorder.service',
    'pi/services/van-dashboard.service': '/etc/systemd/system/van-dashboard.service',
    'pi/scripts/openwrt-logging/30-openwrt-dendelion.conf': '/etc/rsyslog.d/30-openwrt-dendelion.conf',
    'pi/scripts/openwrt-logging/rotate_network_log.py': '/usr/local/libexec/vanpi-rotate-network-log',
}
for name in ('__init__', 'collector', 'parsers', 'report', 'store'):
    TARGETS[f'pi/scripts/network_recorder/{name}.py'] = f'/home/pi/scripts/network_recorder/{name}.py'

# Importing the redaction shim also imports the CLI and its package dependencies.
# These are checked prerequisites, never managed deployment targets.
MONITOR_PACKAGE_DEPENDENCIES = {
    f'pi/scripts/system_monitor/{name}.py': f'/home/pi/scripts/system_monitor/{name}.py'
    for name in ('__init__', 'cli', 'common', 'crash', 'daemon', 'journal', 'probes', 'report', 'rollups', 'store')
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with Path(path).open('rb') as stream:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
        return result.hexdigest()


def regular_info(path):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'refusing non-regular managed file: {path}')
    return dict(sha256=digest(path), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)


def safe_release(value):
    if not re.fullmatch(r'network-[A-Za-z0-9._-]+', value):
        raise ValueError('invalid release ID')
    return value


def link_info(path):
    if not path.is_symlink():
        if path.exists():
            raise ValueError(f'expected release symlink: {path}')
        return None
    value = os.readlink(path)
    if not re.fullmatch(r'releases/[A-Za-z0-9._-]+', value):
        raise ValueError('unexpected frontend release link')
    if not (path.parent / value).is_dir():
        raise ValueError('missing frontend release')
    return value


def tree_digest(path):
    entries = []
    total = 0
    for item in sorted(Path(path).rglob('*')):
        if item.is_symlink():
            raise ValueError('unexpected symlink inside frontend release')
        if item.is_file():
            total += item.stat().st_size
            if len(entries) >= 1000 or total > 64 * 1024 * 1024:
                raise ValueError('frontend release exceeds review budget')
            entries.append([str(item.relative_to(path)), digest(item)])
    return sha(json.dumps(entries, separators=(',', ':')).encode())


def command(args, check=True):
    return subprocess.run(args, capture_output=True, text=True, check=check)


def service_state(name):
    enabled = command(['/usr/bin/systemctl', 'is-enabled', name], False)
    active = command(['/usr/bin/systemctl', 'is-active', name], False)
    return dict(enabled=enabled.stdout.strip() or 'not-found', active=active.stdout.strip() == 'active')


def refuse_managed_host(storage_message='network storage has migrated; use deploy_network_storage.py'):
    if STORAGE_CONFIG.exists():
        raise ValueError(storage_message)
    try:
        PACKAGE_ACTIVATION.lstat()
    except FileNotFoundError:
        return
    raise ValueError('dashboard package is activated; use deploy_network_storage.py for recorder changes '
                     'and deploy_python.py --update for dashboard changes')


def validate_plan(plan, *, require_monitor_package_dependencies=True):
    """Only historical saved manifests may predate the package dependency pins."""
    safe_release(plan['release'])
    if plan.get('schema_version') != 1:
        raise ValueError('unsupported deployment manifest')
    dependencies = {}
    if require_monitor_package_dependencies or 'monitor_package_dependencies' in plan:
        dependencies = plan.get('monitor_package_dependencies')
        if not isinstance(dependencies, dict) or set(dependencies) != set(MONITOR_PACKAGE_DEPENDENCIES.values()):
            raise ValueError('manifest differs from system monitor dependency allowlist')
    for value in [plan.get('monitor_dependency'), *dependencies.values()]:
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('invalid system monitor dependency checksum')
    expected = set(TARGETS.items())
    supplied = {(entry['source'], entry['destination']) for entry in plan['files']}
    if supplied != expected or len(plan['files']) != len(expected):
        raise ValueError('manifest differs from managed target allowlist')
    for entry in plan['frontend_files']:
        path = Path(entry['relative'])
        if path.is_absolute() or '..' in path.parts or str(path) in ('BUILD_ID', 'react_dashboard_preview.py'):
            raise ValueError('unsafe frontend artifact path')
    if not any(entry['relative'] == 'index.html' for entry in plan['frontend_files']):
        raise ValueError('frontend index is missing')


def inspect_remote(plan):
    refuse_managed_host()
    validate_plan(plan)
    current = link_info(FRONTEND / 'current')
    prior_files = {}
    if current and current.startswith('releases/network-'):
        prior_root = DEPLOY_ROOT / safe_release(current.split('/', 1)[1])
        if (prior_root / 'complete').is_file() and not (prior_root / 'rolled-back').exists():
            prior = json.loads((prior_root / 'manifest.json').read_text())
            validate_plan(prior, require_monitor_package_dependencies=False)
            prior_files = {entry['destination']: entry['sha256'] for entry in prior['files']}
    mismatches = []
    for entry in plan['files']:
        info = regular_info(entry['destination'])
        entry['before'] = info
        if info is not None and info['sha256'] not in (entry['baseline_sha256'], entry['sha256'], prior_files.get(entry['destination'])):
            mismatches.append(entry['destination'])
    if mismatches:
        raise ValueError('live files differ from HEAD and reviewed desired version: ' + ', '.join(mismatches))
    dependency = plan['monitor_dependency']
    info = regular_info('/home/pi/scripts/system_event_monitor.py')
    if info is None or info['sha256'] != dependency:
        raise ValueError('existing system monitor redaction dependency differs from reviewed checkout')
    for path, expected in plan['monitor_package_dependencies'].items():
        info = regular_info(path)
        if info is None or info['sha256'] != expected:
            raise ValueError(f'existing system monitor dependency differs from reviewed checkout: {path}')
    plan['frontend_before'] = {name: link_info(FRONTEND / name) for name in ('current', 'previous')}
    current = plan['frontend_before']['current']
    if current is None:
        raise ValueError('existing dashboard frontend release required')
    plan['frontend_before']['tree_sha256'] = tree_digest(FRONTEND / current)
    if not (FRONTEND / current / 'react_dashboard_preview.py').is_file():
        raise ValueError('current preview release lacks existing server')
    plan['services_before'] = {name: service_state(name) for name in SERVICES}
    return plan


def verify_remote(plan):
    refuse_managed_host()
    validate_plan(plan)
    dependency = regular_info('/home/pi/scripts/system_event_monitor.py')
    if dependency is None or dependency['sha256'] != plan['monitor_dependency']:
        raise ValueError('system monitor dependency changed after check')
    for path, expected in plan['monitor_package_dependencies'].items():
        info = regular_info(path)
        if info is None or info['sha256'] != expected:
            raise ValueError(f'system monitor dependency changed after check: {path}')
    for entry in plan['files']:
        if regular_info(entry['destination']) != entry['before']:
            raise ValueError(f'live managed file changed after check: {entry["destination"]}')
    before = plan['frontend_before']
    for name in ('current', 'previous'):
        if link_info(FRONTEND / name) != before[name]:
            raise ValueError('frontend release changed after check')
    if tree_digest(FRONTEND / before['current']) != before['tree_sha256']:
        raise ValueError('frontend contents changed after check')
    if {name: service_state(name) for name in SERVICES} != plan['services_before']:
        raise ValueError('managed service state changed after check')


def atomic_file(source, destination, metadata):
    destination = Path(destination)
    # Refuse symlinked path components, including the managed root.
    if destination.parent.resolve() != destination.parent:
        raise ValueError('symlinked managed destination directory')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.network-install-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output, Path(source).open('rb') as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, metadata['mode'])
        os.chown(temporary, metadata['uid'], metadata['gid'])
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_link(value, path):
    temporary = path.with_name(path.name + '.network-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(value)
        os.replace(temporary, path)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def systemctl(*args):
    return command(['/usr/bin/systemctl', *args])


def cleanup_stage(stage):
    """Remove only verified temporary payload files; never recurse into links."""
    stage = Path(stage)
    if not re.fullmatch(r'/tmp/network-recorder-stage\.[A-Za-z0-9_]+', str(stage)) or stage.is_symlink():
        raise ValueError('unsafe cleanup path')
    # Validate the whole bounded staging tree before deleting anything.
    entries = list(stage.rglob('*'))
    if len(entries) > 1100 or any(item.is_symlink() or not (item.is_file() or item.is_dir()) for item in entries):
        raise ValueError('unexpected staging contents')
    if any(os.path.ismount(item) for item in [stage, *entries] if item.is_dir()):
        raise ValueError('refusing cleanup of a mounted staging tree')
    for item in sorted(entries, key=lambda value: len(value.parts), reverse=True):
        item.rmdir() if item.is_dir() else item.unlink()
    stage.rmdir()


def restore_services(plan):
    systemctl('daemon-reload')
    # Only recorder enablement is changed during install. Restore its exact
    # persistent/runtime/disabled state before restoring prior active states.
    previous = plan['services_before']['network-flight-recorder']['enabled']
    command(['/usr/bin/systemctl', 'disable', 'network-flight-recorder'], False)
    if previous in ('enabled', 'enabled-runtime'):
        args = ['enable'] + (['--runtime'] if previous == 'enabled-runtime' else [])
        systemctl(*args, 'network-flight-recorder')
    for name in ('rsyslog', 'van-dashboard', 'van-dashboard-preview', 'network-flight-recorder'):
        if plan['services_before'][name]['active']:
            systemctl('restart', name)
        else:
            command(['/usr/bin/systemctl', 'stop', name], False)


def rollback_remote(release, automatic=False):
    refuse_managed_host('network storage has migrated; use deploy_network_storage.py rollback')
    root = DEPLOY_ROOT / safe_release(release)
    plan = json.loads((root / 'manifest.json').read_text())
    validate_plan(plan, require_monitor_package_dependencies=False)
    if not automatic:
        completed = (root / 'complete').is_file()
        for entry in plan['files']:
            current = regular_info(entry['destination'])
            allowed = [entry['sha256']]
            if not completed:
                allowed.append(entry['before']['sha256'] if entry['before'] else None)
            if (current['sha256'] if current else None) not in allowed:
                raise ValueError('managed files changed since this deployment; refusing destructive rollback')
        current_link = link_info(FRONTEND / 'current')
        allowed_links = ['releases/' + release]
        if not completed:
            allowed_links.append(plan['frontend_before']['current'])
        if current_link not in allowed_links:
            raise ValueError('frontend is no longer this deployment; refusing rollback')
        if current_link == 'releases/' + release and tree_digest(FRONTEND / current_link) != plan.get('installed_tree_sha256'):
            raise ValueError('frontend changed since this deployment; refusing rollback')
    command(['/usr/bin/systemctl', 'stop', 'network-flight-recorder'], False)
    command(['/usr/bin/systemctl', 'disable', 'network-flight-recorder'], False)
    for index, entry in enumerate(plan['files']):
        destination = Path(entry['destination'])
        if entry['before'] is None:
            if destination.exists():
                if (destination.parent.resolve() != destination.parent or not destination.is_file()
                        or destination.is_symlink() or os.path.ismount(destination)):
                    raise ValueError('unexpected rollback destination type')
                destination.unlink()
        else:
            atomic_file(root / f'before-{index}', destination, entry['before'])
    for name in ('current', 'previous'):
        value = plan['frontend_before'][name]
        if value is None:
            if (FRONTEND / name).is_symlink():
                (FRONTEND / name).unlink()
        else:
            atomic_link(value, FRONTEND / name)
    restore_services(plan)
    (root / 'rolled-back').write_text(str(time.time()) + '\n')
    return dict(ok=True, rolled_back=release, database='preserved')


def apply_remote(plan, stage):
    refuse_managed_host()
    import pwd
    validate_plan(plan)
    verify_remote(plan)
    stage = Path(stage)
    if not re.fullmatch(r'/tmp/network-recorder-stage\.[A-Za-z0-9_]+', str(stage)) or stage.is_symlink():
        raise ValueError('unsafe staging directory')
    expected = {'files/' + str(i) for i in range(len(plan['files']))}
    expected |= {'frontend/' + item['relative'] for item in plan['frontend_files']}
    unpacked = stage / 'payload'
    unpacked.mkdir(mode=0o700)
    with tarfile.open(stage / 'payload.tar') as archive:
        members = archive.getmembers()
        if len(members) != len(expected) or {item.name for item in members} != expected or any(not item.isfile() for item in members):
            raise ValueError('unexpected files in deployment archive')
        for member in members:
            target = unpacked / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
    for index, entry in enumerate(plan['files']):
        if digest(unpacked / 'files' / str(index)) != entry['sha256']:
            raise ValueError('staged file checksum mismatch')
        if entry['source'].endswith('.py'):
            compile((unpacked / 'files' / str(index)).read_text(), entry['source'], 'exec')
    for entry in plan['frontend_files']:
        if digest(unpacked / 'frontend' / entry['relative']) != entry['sha256']:
            raise ValueError('staged frontend checksum mismatch')
    receiver = next(i for i, item in enumerate(plan['files']) if item['destination'].endswith('30-openwrt-dendelion.conf'))
    command(['/usr/sbin/rsyslogd', '-N1', '-f', str(unpacked / 'files' / str(receiver))])
    root = DEPLOY_ROOT / plan['release']
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root.parent, 0o700)
    for index, entry in enumerate(plan['files']):
        if entry['before'] is not None:
            shutil.copyfile(entry['destination'], root / f'before-{index}')
    (root / 'manifest.json').write_text(json.dumps(plan, indent=2) + '\n')
    new_frontend = FRONTEND / 'releases' / plan['release']
    if new_frontend.exists():
        raise ValueError('frontend release already exists')
    verify_remote(plan)
    try:
        command(['/usr/bin/systemctl', 'stop', 'network-flight-recorder'], False)
        pi = pwd.getpwnam('pi')
        for index, entry in enumerate(plan['files']):
            metadata = entry['before'] or dict(uid=pi.pw_uid if entry['destination'].startswith('/home/pi/') else 0,
                                                gid=pi.pw_gid if entry['destination'].startswith('/home/pi/') else 0,
                                                mode=0o755 if entry['destination'].endswith(('network_flight_recorder.py', 'vanpi-rotate-network-log')) else 0o644)
            atomic_file(unpacked / 'files' / str(index), entry['destination'], metadata)
        command(['/usr/sbin/rsyslogd', '-N1'])
        shutil.copytree(unpacked / 'frontend', new_frontend)
        shutil.copyfile(FRONTEND / plan['frontend_before']['current'] / 'react_dashboard_preview.py', new_frontend / 'react_dashboard_preview.py')
        (new_frontend / 'BUILD_ID').write_text(plan['release'] + '\n')
        for item in [new_frontend, *new_frontend.rglob('*')]:
            item.chmod(0o755 if item.is_dir() else 0o644)
        plan['installed_tree_sha256'] = tree_digest(new_frontend)
        (root / 'manifest.json').write_text(json.dumps(plan, indent=2) + '\n')
        atomic_link(plan['frontend_before']['current'], FRONTEND / 'previous')
        atomic_link('releases/' + plan['release'], FRONTEND / 'current')
        systemctl('daemon-reload')
        systemctl('restart', 'rsyslog')
        systemctl('enable', 'network-flight-recorder')
        systemctl('restart', 'network-flight-recorder')
        for name in ('van-dashboard', 'van-dashboard-preview'):
            if plan['services_before'][name]['active']:
                systemctl('restart', name)
        for _ in range(30):
            active = all(service_state(name)['active'] for name in ('network-flight-recorder', 'rsyslog', 'van-dashboard'))
            if active:
                response = command(['/usr/bin/curl', '-fsS', '--connect-timeout', '1', '--max-time', '3',
                                    'http://127.0.0.1:8788/api/network-history?hours=1'], False)
                if response.returncode == 0 and json.loads(response.stdout).get('ok') is True:
                    break
            time.sleep(1)
        else:
            raise RuntimeError('new services/history endpoint did not become healthy')
        (root / 'complete').write_text(str(time.time()) + '\n')
        return dict(ok=True, release=plan['release'], rollback=str(root), database='preserved')
    except Exception:
        rollback_remote(plan['release'], automatic=True)
        raise


def remote_call(target, action, request):
    source = Path(__file__).read_text()
    source = source[:source.rindex("\nif __name__ == '__main__':")]
    payload = source + '\nrequest = ' + repr(request) + '\n'
    invocation = {'check':'inspect_remote(request)', 'apply':'apply_remote(request["plan"], request["stage"])',
                  'cleanup':'cleanup_stage(request["stage"])',
                  'rollback':'rollback_remote(request["release"])'}[action]
    payload += 'print(json.dumps(' + invocation + '))\n'
    process = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', target,
                              'sudo -n /usr/bin/python3 -'], input=payload, capture_output=True, text=True)
    if process.returncode:
        raise RuntimeError(process.stderr[-3000:])
    return json.loads(process.stdout)


def make_plan(target):
    plan = dict(schema_version=1, target=target, release='network-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8],
                files=[], frontend_files=[], monitor_dependency=digest(REPO / 'pi/scripts/system_event_monitor.py'),
                monitor_package_dependencies={})
    for source, destination in MONITOR_PACKAGE_DEPENDENCIES.items():
        info = regular_info(REPO / source)
        if info is None:
            raise ValueError(f'missing regular system monitor dependency: {source}')
        plan['monitor_package_dependencies'][destination] = info['sha256']
    for source, destination in TARGETS.items():
        path = REPO / source
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'missing regular source file: {source}')
        baseline = subprocess.run(['git', '-C', str(REPO), 'show', 'HEAD:' + source], capture_output=True)
        plan['files'].append(dict(source=source, destination=destination, sha256=digest(path),
                                  baseline_sha256=sha(baseline.stdout) if baseline.returncode == 0 else None))
    dist = REPO / 'pi/apps/van_dashboard/frontend/dist'
    for path in sorted(dist.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlinked frontend build')
        if path.is_file():
            plan['frontend_files'].append(dict(relative=str(path.relative_to(dist)), sha256=digest(path)))
    validate_plan(plan)
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', default='pi@vanpi.lan')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('check', 'apply'):
        sub.add_parser(action).add_argument('--plan', required=True)
    sub.add_parser('rollback').add_argument('--release', required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*', args.target):
        parser.error('unsafe SSH target')
    if args.action == 'check':
        plan_path = Path(args.plan)
        if plan_path.exists():
            raise ValueError('plan path already exists; use a new review file')
        plan = remote_call(args.target, 'check', make_plan(args.target))
        plan_path.write_text(json.dumps(plan, indent=2) + '\n')
        plan_path.chmod(0o600)
        print(json.dumps(dict(ok=True, plan=str(plan_path.absolute()), release=plan['release'],
                              managed_files=len(plan['files']), frontend_files=len(plan['frontend_files']),
                              frontend_before=plan['frontend_before'], services_before=plan['services_before']), indent=2))
    elif args.action == 'apply':
        plan = json.loads(Path(args.plan).read_text())
        validate_plan(plan)
        if plan['target'] != args.target:
            raise ValueError('plan SSH target mismatch')
        current = make_plan(args.target)
        if (current['monitor_dependency'] != plan['monitor_dependency']
                or current['monitor_package_dependencies'] != plan['monitor_package_dependencies']):
            raise ValueError('local system monitor dependency changed after check')
        for key in ('files', 'frontend_files'):
            expected = [{k:v for k,v in item.items() if k != 'before'} for item in plan[key]]
            if current[key] != expected:
                raise ValueError('local sources changed after check; create a fresh plan')
        with tempfile.TemporaryDirectory(prefix='network-recorder-payload-') as directory:
            archive_path = Path(directory) / 'payload.tar'
            with tarfile.open(archive_path, 'w') as archive:
                for index, entry in enumerate(plan['files']):
                    archive.add(REPO / entry['source'], arcname='files/' + str(index), recursive=False)
                for entry in plan['frontend_files']:
                    archive.add(REPO / 'pi/apps/van_dashboard/frontend/dist' / entry['relative'], arcname='frontend/' + entry['relative'], recursive=False)
            stage = command(['ssh', '-o', 'BatchMode=yes', args.target,
                             'mktemp -d /tmp/network-recorder-stage.XXXXXXXX']).stdout.strip()
            if not re.fullmatch(r'/tmp/network-recorder-stage\.[A-Za-z0-9_]+', stage):
                raise ValueError('unexpected remote staging path')
            subprocess.run(['scp', '-q', str(archive_path), args.target + ':' + stage + '/payload.tar'], check=True)
            try:
                print(json.dumps(remote_call(args.target, 'apply', dict(plan=plan, stage=stage)), indent=2))
            finally:
                remote_call(args.target, 'cleanup', dict(stage=stage))
    else:
        print(json.dumps(remote_call(args.target, 'rollback', dict(release=safe_release(args.release))), indent=2))


if __name__ == '__main__':
    main()
