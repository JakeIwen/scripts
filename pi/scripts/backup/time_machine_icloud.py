#!/usr/bin/env python3
"""Weekly consistent, incremental Time Machine replication over guarded rclone.

The shell owns fd 9's existing backup lock. Never copies a mounted Mac image,
never uploads the mutable source, and never publishes before download checks.
"""
from contextlib import contextmanager, suppress
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

import icloud_backup as cloud
import icloud_status as history
import time_machine_store as store

CONFIG = Path('/etc/vanpi-time-machine-icloud.json')
STATE = Path('/var/lib/vanpi-time-machine-icloud')
AUTH = Path('/var/lib/vanpi-icloud-backup')
MOUNT = Path('/mnt/mbp2tbkup')
SOURCE = MOUNT / store.BUNDLE
ROOT = MOUNT / 'archive' / 'icloud-time-machine-v1'
DRAIN = Path('/run/lock/vanpi-samba-drain/mbp2tbkup')
SERVICE = 'vanpi-time-machine-icloud.service'


def config():
    cfg = cloud.load_config()
    cfg.update(store.read_json(CONFIG))
    cfg.setdefault('smb_handle_drain_seconds', 90)
    if cfg['remote'] != 'icloud:VanRecovery/m4mac/time-machine':
        raise ValueError('unexpected Time Machine cloud prefix')
    for key, low, high in (('interval_days', 1, 31), ('keep_generations', 2, 8),
                          ('minimum_free_gib', 20, 500), ('max_cloud_store_gib', 100, 1500),
                          ('max_source_age_hours', 1, 168), ('capture_wait_seconds', 120, 7200),
                          ('smb_handle_drain_seconds', 1, 600),
                          ('capture_max_seconds', 600, 43200)):
        if type(cfg.get(key)) is not int or not low <= cfg[key] <= high:
            raise ValueError('invalid Time Machine setting: ' + key)
    return cfg


def worker_alive(request):
    pid = request.get('pid')
    if type(pid) is not int or pid <= 1:
        return False
    try:
        proc = Path('/proc') / str(pid)
        return (str(Path(__file__).resolve()).encode() in (proc / 'cmdline').read_bytes().split(b'\0')
                and (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19] == request.get('start_ticks')
                and os.readlink(proc / 'fd' / '9') == '/run/lock/vanpi_backup.lock')
    except (OSError, IndexError):
        return False


def capture_request():
    request = store.read_json(STATE / 'capture.json', {})
    if (not request or request.get('expires_at', 0) <= time.time() or
            not worker_alive(request) or not DRAIN.is_file() or DRAIN.is_symlink() or
            DRAIN.read_text() != request.get('marker')):
        return {'requested': False}
    return {'requested': True, 'generation': request['generation'],
            'capture_id': request['capture_id'], 'expires_at': request['expires_at'], 'bundle': store.BUNDLE}


def acknowledge(name, capture_id):
    if not store.GENERATION.fullmatch(name) or not re.fullmatch('[0-9a-f]{32}', capture_id):
        raise ValueError('invalid capture acknowledgement')
    request = capture_request()
    if not request['requested'] or request['generation'] != name or request['capture_id'] != capture_id:
        return {'accepted': False}
    store.atomic_json(STATE / 'capture-ready.json', {'generation': name, 'capture_id': capture_id, 'closed_at': time.time()})
    return {'accepted': True}


def mac_present():
    """A live, functioning Mac helper can wake a due job without hourly delay."""
    now = time.time()
    store.atomic_json(STATE / 'mac-coordinator.json', {'seen_at': now})
    state = store.read_json(STATE / 'state.json', {})
    cfg = config()
    due = state.get('pending') or now - state.get('last_success_at', 0) >= cfg['interval_days'] * 86400
    retry = now - state.get('last_attempt_at', 0) >= 3600 or state.get('last_error') == 'Mac capture coordinator unavailable'
    if due and retry:
        subprocess.run(['/usr/bin/systemctl', 'start', '--no-block', SERVICE], check=True, timeout=10)
    return {'ok': True}


def release_capture(own=False):
    request = store.read_json(STATE / 'capture.json', {})
    if not request:
        return
    if worker_alive(request) and (not own or request.get('pid') != os.getpid()):
        raise RuntimeError('refusing to release a live capture gate')
    # Do not remove a shutdown marker installed by another disk lifecycle job.
    if DRAIN.exists() and not DRAIN.is_symlink() and DRAIN.read_text() == request.get('marker'):
        DRAIN.unlink()
    (STATE / 'capture.json').unlink(missing_ok=True)
    (STATE / 'capture-ready.json').unlink(missing_ok=True)


def smb_state():
    value = json.loads(cloud.capture(['/usr/bin/smbstatus', '--json']))
    if not isinstance(value.get('open_files'), dict) or not isinstance(value.get('tcons'), dict):
        raise RuntimeError('cannot establish Samba handle state')
    handles = [row for row in value['open_files'].values()
               if row.get('service_path') == str(MOUNT) and
               (row.get('filename', '') == store.BUNDLE or row.get('filename', '').startswith(store.BUNDLE + '/'))]
    trees = [row for row in value['tcons'].values() if row.get('service') == 'mbp2tbkup']
    return len(handles), len(trees)


def wait_image_handles(cfg, state, marker):
    """A clean Mac detach can precede the SMB client's deferred CLOSEs.

    Wait boundedly for ZERO image handles; never force-close them or interpret
    a failed Samba probe as an empty result.
    """
    deadline = time.monotonic() + cfg.get('smb_handle_drain_seconds', 90)
    while smb_state()[0]:
        cloud.check_work_allowed(cfg)
        cloud.exact_mount(MOUNT, 'mbp2tbkup')
        if DRAIN.is_symlink() or not DRAIN.is_file() or DRAIN.read_text() != marker:
            raise cloud.Deferred('Time Machine capture lost exclusive source access')
        if time.monotonic() >= deadline:
            raise cloud.Deferred('Mac acknowledgement received but image handles remain open')
        cloud.record_progress(state, 'preparing', capture_waiting=False, capture_draining=True)
        time.sleep(2)
    cloud.record_progress(state, 'preparing', capture_waiting=False, capture_draining=False)


@contextmanager
def quiesced(cfg, state):
    release_capture()  # Recover only this job's orphaned marker, never a live one.
    cloud.exact_mount(MOUNT, 'mbp2tbkup')
    if DRAIN.parent.is_symlink() or not DRAIN.parent.is_dir() or DRAIN.parent.stat().st_uid != 0:
        raise RuntimeError('unsafe Samba drain directory')
    capture_id = uuid.uuid4().hex
    marker = store.OWNER + ':' + capture_id + '\n'
    request = {'pid': os.getpid(), 'start_ticks': Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
               'generation': state['pending'], 'capture_id': capture_id, 'marker': marker,
               'expires_at': time.time() + cfg['capture_wait_seconds']}
    # Publish recovery metadata before gate creation, so ExecStopPost can clean
    # up even if the worker is killed immediately after creating its marker.
    store.atomic_json(STATE / 'capture.json', request)
    try:
        with DRAIN.open('x') as f:
            f.write(marker); f.flush(); os.fsync(f.fileno())
    except FileExistsError:
        (STATE / 'capture.json').unlink()
        raise cloud.Deferred('Time Machine share is already draining')
    try:
        cloud.record_progress(state, 'preparing', capture_waiting=True, capture_draining=False,
                              capture_bytes=0, capture_total_bytes=None)
        print('Waiting for Mac to cleanly detach its idle Time Machine image.', flush=True)
        while True:
            cloud.check_work_allowed(cfg)
            ack = store.read_json(STATE / 'capture-ready.json', {})
            if ack.get('capture_id') == capture_id and ack.get('generation') == state['pending']:
                break
            if time.time() >= request['expires_at']:
                raise cloud.Deferred('waiting for Mac clean detach; coordinator may need installation')
            cloud.record_progress(state, 'preparing', capture_waiting=True)
            time.sleep(5)
        wait_image_handles(cfg, state, marker)
        # The image has been cleanly detached. Closing only its now-idle share
        # prevents an old tree connection bypassing the already-active preexec gate.
        cloud.capture(['/usr/bin/smbcontrol', 'smbd', 'close-share', 'mbp2tbkup'])
        time.sleep(2)
        if smb_state() != (0, 0):
            raise cloud.Deferred('Time Machine SMB share did not quiesce')
        deadline = time.monotonic() + cfg['capture_max_seconds']
        next_check = [0.0]
        def check():
            if time.monotonic() < next_check[0]:
                return
            cloud.check_work_allowed(cfg)
            cloud.exact_mount(MOUNT, 'mbp2tbkup')
            if time.monotonic() >= deadline:
                raise cloud.Deferred('capture window ended; normal Mac backups may resume')
            if not DRAIN.is_file() or DRAIN.is_symlink() or DRAIN.read_text() != marker or smb_state() != (0, 0):
                raise cloud.Deferred('Time Machine capture lost exclusive source access')
            cloud.record_progress(state, 'preparing', capture_waiting=False)
            next_check[0] = time.monotonic() + cfg['guard_interval_seconds']
        check()
        yield check
    finally:
        release_capture(own=True)


def private_directory(path):
    if path.is_symlink():
        raise RuntimeError('unsafe private storage directory')
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    s = path.stat()
    if s.st_uid != 0 or s.st_mode & 0o077:
        raise RuntimeError('private storage must be root-owned mode 0700')


def storage_root():
    cloud.exact_mount(MOUNT, 'mbp2tbkup')
    if (MOUNT / 'archive').is_symlink() or not (MOUNT / 'archive').is_dir():
        raise RuntimeError('archive directory is unsafe or absent')
    private_directory(ROOT)
    marker = ROOT / 'OWNER.json'
    owner = {'owner': store.OWNER}
    if marker.exists():
        if store.read_json(marker) != owner:
            raise RuntimeError('local store owner mismatch')
    elif any(ROOT.iterdir()):
        raise RuntimeError('refusing to claim a nonempty store')
    else:
        store.atomic_json(marker, owner)
    private_directory(ROOT / 'objects')
    private_directory(ROOT / 'generations')
    return ROOT


def network(cfg, *args, **kwargs):
    return cloud.run([cloud.RCLONE, *args], cfg, network=True, **kwargs)


def ensure_remote(cfg):
    remote = cfg['remote']
    network(cfg, 'mkdir', remote)
    entries = json.loads(network(cfg, 'lsjson', remote, '--max-depth', '1'))
    if not any(e.get('Name') == 'OWNER.json' for e in entries):
        if entries:
            raise RuntimeError('refusing to claim a nonempty cloud store')
        network(cfg, 'copyto', str(ROOT / 'OWNER.json'), remote + '/OWNER.json', '--immutable')
    if json.loads(network(cfg, 'cat', remote + '/OWNER.json')) != {'owner': store.OWNER}:
        raise RuntimeError('cloud store owner mismatch')
    network(cfg, 'mkdir', remote + '/objects')
    network(cfg, 'mkdir', remote + '/generations')


def transfer(cfg, manifest, state):
    expected = store.validate_manifest(manifest)
    remote = cfg['remote']
    objects_remote = remote + '/objects'
    ensure_remote(cfg)
    rows = cloud.remote_inventory(cfg, objects_remote)
    missing = sum(v['bytes'] for k, v in expected.items() if rows.get(k, {}).get('Size') != v['bytes'])
    # The iCloud backend has no account quota API. Bound this dedicated store,
    # leaving room for Pi copies and other iCloud data; provider quota failures
    # still stop safely without retiring a prior recovery point.
    if sum(row['Size'] for row in rows.values()) + missing > cfg['max_cloud_store_gib'] * 1024**3:
        raise cloud.Deferred('Time Machine iCloud storage budget reached; previous verified copies preserved')
    files = ROOT / 'upload-files.txt'
    # All entries are validated lowercase SHA-256 names, not caller paths.
    files.write_text(''.join(name + '\n' for name in sorted(expected)))
    cloud.record_progress(state, 'uploading', **cloud.upload_progress(expected, rows, {}))
    network(cfg, 'copy', str(ROOT / 'objects'), objects_remote,
            '--files-from-raw', str(files), '--immutable', '--size-only',
            progress=lambda counters: cloud.record_progress(state, 'uploading',
                **cloud.upload_progress(expected, rows, counters)))
    total = sum(x['bytes'] for x in expected.values())
    cloud.record_progress(state, 'verifying', upload_estimated_bytes=total)
    verify_objects(cfg, expected, state)
    name = manifest['generation']
    gen = ROOT / 'generations' / name
    private_directory(gen)
    store.atomic_json(gen / 'manifest.json', manifest)
    manifest_hash = hashlib.sha256((gen / 'manifest.json').read_bytes()).hexdigest()
    remote_gen = remote + '/generations/' + name
    cloud.record_progress(state, 'publishing')
    network(cfg, 'copyto', str(gen / 'manifest.json'), remote_gen + '/manifest.json', '--immutable', '--size-only')
    if hashlib.sha256(network(cfg, 'cat', remote_gen + '/manifest.json').encode()).hexdigest() != manifest_hash:
        raise RuntimeError('cloud manifest read-back mismatch')
    for basename in ('time_machine_store.py', 'TIME_MACHINE_ICLOUD_RESTORE.txt'):
        network(cfg, 'copyto', str(Path(__file__).with_name(basename)), remote + '/' + basename)
    proof = {'owner': store.OWNER, 'generation': name,
             'verification': 'sha256-download-compared', 'manifest_sha256': manifest_hash,
             'objects': len(expected), 'bytes': total}
    marker = store.read_json(gen / '_COMPLETE.json')
    if marker is None:
        marker = {**proof, 'completed_at': time.time()}
        store.atomic_json(gen / '_COMPLETE.json', marker)
    elif any(marker.get(k) != v for k, v in proof.items()) or type(marker.get('completed_at')) not in (int, float):
        raise RuntimeError('existing completion marker identity mismatch')
    network(cfg, 'copyto', str(gen / '_COMPLETE.json'), remote_gen + '/_COMPLETE.json', '--immutable', '--size-only')
    if json.loads(network(cfg, 'cat', remote_gen + '/_COMPLETE.json')) != marker:
        raise RuntimeError('cloud completion marker read-back mismatch')
    state.update(last_success_at=marker['completed_at'], last_generation=name, pending=None,
                 attempt_verified_at=marker['completed_at'])
    cloud.record_progress(state, 'complete')
    history.save_attempt(STATE, state)
    return name


def verify_objects(cfg, expected, state):
    remote = cfg['remote'] + '/objects'
    rows = cloud.remote_inventory(cfg, remote)
    cache_path = ROOT / 'verified-objects.json'
    cache = store.read_json(cache_path, {})
    verified = {}
    for digest, entry in expected.items():
        fp = cloud.remote_fingerprint(rows.get(digest))
        if fp is None or fp['size'] != entry['bytes']:
            raise RuntimeError('cloud object missing or wrong size')
        if cache.get(digest, {}).get('remote') == fp and cache[digest].get('expected') == entry:
            verified[digest] = cache[digest]
    total = sum(v['bytes'] for v in expected.values())
    def report(**extra):
        cloud.record_progress(state, 'verifying', verified_files=len(verified),
            verification_total_files=len(expected), verified_bytes=sum(expected[k]['bytes'] for k in verified),
            verification_total_bytes=total, **extra)
    report(current_file_bytes=0)
    for digest in sorted(expected, key=lambda k: (expected[k]['bytes'], k)):
        if digest in verified:
            continue
        result = network(cfg, 'cat', remote + '/' + digest, stream_hash=True,
                         progress=lambda c: report(current_file_bytes=c.get('download_bytes', 0)))
        if result != expected[digest]:
            raise RuntimeError('cloud object failed downloaded SHA-256 check')
        cache[digest] = {'expected': expected[digest], 'remote': cloud.remote_fingerprint(rows[digest]),
                         'verified_at': time.time()}
        store.atomic_json(cache_path, cache)
        verified[digest] = cache[digest]
        report(current_file_bytes=0)
    final = cloud.remote_inventory(cfg, remote)
    changed = [k for k in expected if cloud.remote_fingerprint(final.get(k)) != verified[k]['remote']]
    if changed:
        for k in changed:
            cache.pop(k, None)
        store.atomic_json(cache_path, cache)
        raise cloud.Deferred('cloud objects changed during verification; checkpoints invalidated')


def retention(cfg, current):
    """Validate all manifests before retiring copies; never prune to make space."""
    remote = cfg['remote']
    rows = json.loads(network(cfg, 'lsjson', remote + '/generations', '--dirs-only'))
    journal_path = ROOT / 'retiring.json'
    retiring = store.read_json(journal_path, [])
    if not isinstance(retiring, list) or any(not store.GENERATION.fullmatch(n) or n >= current for n in retiring):
        raise RuntimeError('invalid retirement journal')
    present = {row.get('Name'): row for row in rows}
    manifests, complete = {}, []
    for row in rows:
        name = row.get('Name', '')
        if not row.get('IsDir') or not store.GENERATION.fullmatch(name):
            raise RuntimeError('unexpected cloud generation; retention refused')
        path = remote + '/generations/' + name
        if name in retiring:
            # A previous attempt may have removed one or both metadata files.
            # Re-prove the retained copies below before continuing retirement.
            continue
        raw = network(cfg, 'cat', path + '/manifest.json')
        manifest = json.loads(raw)
        manifests[name] = store.validate_manifest(manifest)
        if manifest['generation'] != name:
            raise RuntimeError('retention generation mismatch')
        entries = json.loads(network(cfg, 'lsjson', path, '--files-only'))
        if any(e.get('Name') == '_COMPLETE.json' for e in entries):
            marker = json.loads(network(cfg, 'cat', path + '/_COMPLETE.json'))
            if (marker.get('owner') != store.OWNER or marker.get('generation') != name or
                    marker.get('verification') != 'sha256-download-compared' or
                    marker.get('manifest_sha256') != hashlib.sha256(raw.encode()).hexdigest()):
                raise RuntimeError('retention completion proof mismatch')
            complete.append(name)
    if current not in complete:
        raise RuntimeError('new verified generation absent; retention refused')
    if retiring and len(complete) < cfg['keep_generations']:
        raise RuntimeError('retained recovery copies missing; retirement refused')
    retired = sorted(set(retiring + sorted(complete)[:-cfg['keep_generations']]))
    store.atomic_json(journal_path, retired)
    for name in retired:
        # Only these two validated metadata files, never a recursive cloud purge.
        base = remote + '/generations/' + name
        if name in present:
            entries = json.loads(network(cfg, 'lsjson', base, '--files-only'))
            names = {entry.get('Name') for entry in entries}
            if not names <= {'_COMPLETE.json', 'manifest.json'}:
                raise RuntimeError('unexpected generation contents; retirement refused')
            for filename in ('_COMPLETE.json', 'manifest.json'):
                if filename in names:
                    network(cfg, 'deletefile', base + '/' + filename)
            network(cfg, 'rmdir', base)
        manifests.pop(name, None)
    store.atomic_json(journal_path, [])
    referenced = {key for manifest in manifests.values() for key in manifest}
    objects = cloud.remote_inventory(cfg, remote + '/objects')
    for digest in objects:
        if not store.DIGEST.fullmatch(digest):
            raise RuntimeError('unknown object name; garbage collection refused')
    for digest in objects.keys() - referenced:
        network(cfg, 'deletefile', remote + '/objects/' + digest)
    # Source capture cache can be invalidated safely if an old local object is
    # retired. An interrupted capture/upload is never present during retention.
    cloud.exact_mount(MOUNT, 'mbp2tbkup')
    for entry in (ROOT / 'objects').iterdir():
        if store.DIGEST.fullmatch(entry.name) and entry.name not in referenced:
            store.signature(entry)  # Refuse symlinks/non-files before unlink.
            entry.unlink()


def weekly(cfg, state):
    if time.time() - state.get('last_success_at', 0) < cfg['interval_days'] * 86400 and not state.get('pending'):
        return 'not due'
    if not cloud.credentials_ready(cfg) or not (AUTH / 'authenticated.json').is_file() or (AUTH / 'authentication-error.json').exists():
        raise cloud.Deferred('iCloud login is required; use the existing Pi login command')
    cloud.check_work_allowed(cfg)
    cloud.guard(cfg)
    storage_root()
    manifest = store.read_json(ROOT / 'pending.json')
    if not state.get('pending'):
        state['pending'] = 'tm-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        state['progress'] = {}
        cloud.record_progress(state, 'checking')
    if not manifest or manifest.get('generation') != state['pending']:
        presence = store.read_json(STATE / 'mac-coordinator.json', {})
        if not 0 <= time.time() - presence.get('seen_at', 0) <= 300:
            # Do not block the Mac's ordinary hourly backups while its helper
            # is not installed, asleep or unable to inspect Time Machine.
            raise cloud.Deferred('Mac capture coordinator unavailable')
        with quiesced(cfg, state) as check:
            last_report = [0.0]
            def report(done, total, files, count):
                if time.monotonic() - last_report[0] >= cfg['guard_interval_seconds'] or files == count:
                    cloud.record_progress(state, 'preparing', capture_waiting=False,
                        capture_bytes=done, capture_total_bytes=total, capture_files=files, capture_total_files=count)
                    last_report[0] = time.monotonic()
            manifest = store.freeze(SOURCE, ROOT, state['pending'], cfg['max_source_age_hours'],
                                    cfg['minimum_free_gib'] * 1024**3, check, report)
        print('Frozen encrypted image captured; normal Mac backups are allowed again.', flush=True)
    name = transfer(cfg, manifest, state)
    cloud.record_progress(state, 'retention')
    retention(cfg, name)
    cloud.notify(state, 'Mac Time Machine iCloud copy verified',
                 'Encrypted Time Machine recovery copy uploaded and download-verified: ' + name)
    return 'complete'


def main():
    os.umask(0o077)
    private_directory(STATE)
    args = sys.argv[1:]
    if args == ['--capture-request']:
        print(json.dumps(capture_request())); return 0
    if args == ['--mac-present']:
        print(json.dumps(mac_present())); return 0
    if len(args) == 3 and args[0] == '--capture-ready':
        print(json.dumps(acknowledge(args[1], args[2]))); return 0
    if args == ['--release-capture']:
        release_capture(); return 0
    if args == ['--status']:
        print(json.dumps(store.read_json(STATE / 'state.json', {}), indent=2)); return 0
    if args != ['--run']:
        raise ValueError('unsupported operation')
    cloud.STATE_DIR = STATE
    cfg = config()
    state = store.read_json(STATE / 'state.json', {})
    state['kind'] = 'time-machine'
    signal.signal(signal.SIGTERM, cloud.abort)
    signal.signal(signal.SIGINT, cloud.abort)
    due = state.get('pending') or time.time() - state.get('last_success_at', 0) >= cfg['interval_days'] * 86400
    if due:
        history.begin_attempt(STATE, state)
    else:
        state.pop('attempt_id', None)
    try:
        state.update(worker_pid=os.getpid(), phase='checking', last_attempt_at=time.time(), last_error=None)
        cloud.record_progress(state, 'checking')
        state['phase'] = weekly(cfg, state)
        return 0
    except cloud.AuthenticationRequired as exc:
        state.update(phase='authentication_required', last_error=str(exc))
        # Share the login canary/authentication gate with the Pi job.
        store.atomic_json(AUTH / 'authentication-error.json', {'failed_at': time.time(), 'reason': str(exc)})
        cloud.notify(state, 'Mac Time Machine iCloud login needed', 'Renew the existing Pi iCloud login privately.')
        return 1
    except cloud.Deferred as exc:
        state.update(phase='deferred', last_error=str(exc))
        print('Deferred: ' + str(exc), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        state.update(phase='error', last_error=message)
        print('Failed: ' + message, flush=True)
        cloud.notify(state, 'Mac Time Machine iCloud copy needs attention', 'No new recovery point was published. Previous copies are preserved.')
        return 1
    finally:
        store.atomic_json(STATE / 'state.json', state)
        history.save_attempt(STATE, state, finished=True)


if __name__ == '__main__':
    sys.exit(main())
