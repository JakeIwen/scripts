#!/usr/bin/env python3
"""Guarded flash/RAM recorder migration, updates and history-preserving rollback.

check --plan /tmp/network-storage-plan.json
apply --plan /tmp/network-storage-plan.json
rollback --release network-storage-RELEASE

No mounts, routes, backup jobs, or Git state are changed. The only backup change
is adding two exclusions to the deployed BORG_EXCLUDES array, preserving all
other deployed configuration bytes. Flash writes use an open verified directory
descriptor so a disappearing mount cannot redirect writes onto the SD card.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid

import deploy_network_flight_recorder as base

REPO = Path(__file__).resolve().parents[1]
CONFIG = Path('/etc/vanpi-network-storage.json')
BACKUPS = Path('/var/lib/vanpi-network-deploy/storage')
BACKUP_CONF = Path('/home/pi/scripts/backup/backup_conf.sh')
OLD_DB = Path('/var/lib/vanpi-network/events.sqlite3')
OLD_LOGS = Path('/var/log/openwrt')
SERVICES = ('network-flight-recorder', 'rsyslog', 'van-dashboard', 'van-dashboard-preview')
TARGETS = {
    'pi/scripts/network_flight_recorder.py':'/home/pi/scripts/network_flight_recorder.py',
    'pi/apps/van_dashboard/van_dashboard_history.py':'/home/pi/scripts/python-automation/van_dashboard_history.py',
    'pi/services/network-flight-recorder.service':'/etc/systemd/system/network-flight-recorder.service',
    'pi/services/rsyslog-vanpi-network.conf':'/etc/systemd/system/rsyslog.service.d/vanpi-network.conf',
    'pi/services/van-dashboard-network-storage.conf':'/etc/systemd/system/van-dashboard.service.d/network-storage.conf',
    'pi/tmpfiles.d/vanpi-network.conf':'/etc/tmpfiles.d/vanpi-network.conf',
    'pi/services/network-flight-recorder-storage.json':str(CONFIG),
    'pi/scripts/openwrt-logging/30-openwrt-dendelion.conf':'/etc/rsyslog.d/30-openwrt-dendelion.conf',
    'pi/scripts/openwrt-logging/rotate_network_log.py':'/usr/local/libexec/vanpi-rotate-network-log',
    'pi/scripts/openwrt-logging/openwrt-dendelion-retired.logrotate':'/etc/logrotate.d/openwrt-dendelion',
    'pi/scripts/setup_openwrt_logging.sh':'/home/pi/scripts/setup_openwrt_logging.sh',
}
for name in ('__init__','collector','parsers','report','store','storage'):
    TARGETS[f'pi/scripts/network_recorder/{name}.py'] = f'/home/pi/scripts/network_recorder/{name}.py'

# Read-only inspection on 2026-09-30 verified these prior recorder versions
# against the checkout before storage work began. Later updates use the last
# successful storage manifest; unrelated deployed edits still fail closed.
INITIAL = {
 '/home/pi/scripts/network_flight_recorder.py':'1af4fb0e0a4e73506e02776a96119d22ca2808d3c09758d9d6f183fb168b2c27',
 '/home/pi/scripts/network_recorder/__init__.py':'8e287c6cb7410d3ab1364a779677935e30dd071c4bf8032be40e6652e79f4f69',
 '/home/pi/scripts/network_recorder/collector.py':'7691ac5ddfb062e59e994b77676bfd5993be18155858dede5f08c906d0601bdd',
 '/home/pi/scripts/network_recorder/store.py':'4370a1f87f939d6bc2260bf639bad3fdb74546f44997999260b619c71390aeb9',
 '/home/pi/scripts/network_recorder/report.py':'6f1fb154b8d7c4b810dea436f0f1697c9066c84343259198d31f5e657ab151fb',
 '/home/pi/scripts/network_recorder/parsers.py':'9f7fdc6e6b5afe4b4661989f3ad61fc4c929d132821677e17a028c7503d91cdf',
 '/home/pi/scripts/python-automation/van_dashboard_history.py':'cfad30ecd4c8661e584a4d56043eb582ec8af5ec1f0731aed0618f22248a62a9',
 '/etc/systemd/system/network-flight-recorder.service':'15e712b68bcbf6ea0daa18bdcf5b86bb7705db1d3fca1e8b555173a604775b9f',
 '/etc/rsyslog.d/30-openwrt-dendelion.conf':'269cf4095e6ef6bbf6f6f5ffc166761697ee66cb0ebfd5bfb1c99e5cf75ebc7f',
 '/usr/local/libexec/vanpi-rotate-network-log':'c813071bf7e54cb50f6cbfaf66053493b311b5562daded6a4abd4386f8744788',
 '/home/pi/scripts/setup_openwrt_logging.sh':'49244dcfdee48eac5c168b272479dd7003a8c039e26d0d673e017791478c111f',
 '/etc/logrotate.d/openwrt-dendelion':'b27bb933b4605c250091e0dedd1490a7a179f59da6a2465600dc6142ef0929d7',
}


def patch_exclusions(text):
    matches = list(re.finditer(r'(?m)^BORG_EXCLUDES=\(\n(?P<body>.*?)^\)', text, re.S))
    if len(matches) != 1:
        raise ValueError('expected one simple BORG_EXCLUDES array; refusing unrelated backup edits')
    match = matches[0]
    body = match.group('body')
    for path in ('/var/lib/vanpi-network','/var/log/openwrt'):
        if not re.search(r'(?m)^\s*[\"\x27]?' + re.escape(path) + r'[\"\x27]?\s*$', body):
            body += f"  '{path}'\n"
    return text[:match.start('body')] + body + text[match.end('body'):]


def mount_identity(config):
    mount = Path(config['mountpoint'])
    if mount.is_symlink() or mount.resolve() != mount:
        raise ValueError('flash mountpoint must be an exact real directory')
    result = base.command(['/usr/bin/findmnt','--json','--target',str(mount),'-o','SOURCE,TARGET,FSTYPE,UUID,LABEL,OPTIONS,MAJ:MIN'])
    rows = json.loads(result.stdout).get('filesystems',[])
    if len(rows) != 1:
        raise ValueError('flash mount is unavailable or ambiguous')
    row = rows[0]
    if (row['target'] != str(mount) or row['fstype'] != config['fstype'] or
            row.get('uuid','').casefold() != config['uuid'].casefold() or row.get('label') != 'EXFAT512' or
            'rw' not in row.get('options','').split(',')):
        raise ValueError('flash mount identity or writability mismatch')
    dev = mount.stat().st_dev
    if row['maj:min'] != f'{os.major(dev)}:{os.minor(dev)}':
        raise ValueError('mount changed during identity inspection')
    return dict(uuid=row['uuid'],fstype=row['fstype'],label=row['label'],mountpoint=row['target'],device=dev)


class FlashRoot:
    def __init__(self, config, create=False):
        self.config = config
        identity = mount_identity(config)
        self.device = identity['device']
        mount = Path(config['mountpoint'])
        root = Path(config['flash_root'])
        if root.parent != mount or root.name != 'vanpi-network':
            raise ValueError('unexpected flash root')
        mount_fd = os.open(mount,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(mount_fd).st_dev != self.device:
                raise ValueError('flash mount changed before open')
            if create:
                try:
                    os.mkdir(root.name,dir_fd=mount_fd)
                except FileExistsError:
                    pass
            self.fd = os.open(root.name,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=mount_fd)
        finally:
            os.close(mount_fd)
        try:
            self.guard()
        except Exception:
            os.close(self.fd)
            raise

    def guard(self):
        if mount_identity(self.config)['device'] != self.device or os.fstat(self.fd).st_dev != self.device:
            raise ValueError('flash disappeared or changed')

    def directory(self, name):
        self.guard()
        if not re.fullmatch(r'[A-Za-z0-9._-]+',name):
            raise ValueError('unsafe flash directory')
        try:
            os.mkdir(name,dir_fd=self.fd)
        except FileExistsError:
            pass
        child = os.open(name,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=self.fd)
        if os.fstat(child).st_dev != self.device:
            os.close(child)
            raise ValueError('unexpected nested mount')
        return child

    def close(self):
        os.close(self.fd)


def copy_bytes(source, directory_fd, name):
    if not re.fullmatch(r'[A-Za-z0-9._-]+',name):
        raise ValueError('unsafe archive filename')
    if not stat.S_ISREG(Path(source).lstat().st_mode):
        raise ValueError('source is not a regular file')
    descriptor = os.open(name,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o640,dir_fd=directory_fd)
    result = hashlib.sha256()
    with os.fdopen(descriptor,'wb') as target, Path(source).open('rb') as incoming:
        for chunk in iter(lambda: incoming.read(1024*1024),b''):
            target.write(chunk)
            result.update(chunk)
        target.flush()
        os.fsync(target.fileno())
    actual=hashlib.sha256()
    descriptor=os.open(name,os.O_RDONLY | os.O_NOFOLLOW,dir_fd=directory_fd)
    with os.fdopen(descriptor,'rb') as target:
        for chunk in iter(lambda: target.read(1024*1024),b''):
            actual.update(chunk)
    if actual.hexdigest()!=result.hexdigest():
        raise ValueError('flash readback checksum mismatch')
    source_info=Path(source).stat()
    # Keep archive age without applying unsupported FAT ownership/mode bits.
    os.utime(name,ns=(source_info.st_atime_ns,source_info.st_mtime_ns),dir_fd=directory_fd,follow_symlinks=False)
    return result.hexdigest()


def database_snapshot(source, destination):
    original = sqlite3.connect(Path(source).absolute().as_uri() + '?mode=ro',uri=True,timeout=10)
    target = sqlite3.connect(Path(destination).absolute().as_uri() + '?mode=rw',uri=True,timeout=10)
    try:
        original.backup(target,pages=256,sleep=0.02)
        if target.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite migration quick_check failed')
        target.execute('PRAGMA journal_mode=DELETE')
        return {name:target.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in ('events','incidents','checkpoints')}
    finally:
        target.close()
        original.close()


def validate_plan(plan):
    if plan.get('schema_version') != 1 or not re.fullmatch(r'network-storage-[A-Za-z0-9._-]+',plan['release']):
        raise ValueError('invalid storage deployment manifest')
    if {(r['source'],r['destination']) for r in plan['files']} != set(TARGETS.items()) or len(plan['files']) != len(TARGETS):
        raise ValueError('unexpected managed storage targets')
    for row in plan['frontend_files']:
        path=Path(row['relative'])
        if path.is_absolute() or '..' in path.parts or str(path) in ('BUILD_ID','react_dashboard_preview.py'):
            raise ValueError('unsafe frontend artifact')
    if not any(row['relative']=='index.html' for row in plan['frontend_files']):
        raise ValueError('frontend build missing index')


def inspect(plan):
    validate_plan(plan)
    for command in ('/usr/bin/findmnt','/usr/bin/systemd-tmpfiles','/usr/bin/systemctl','/usr/sbin/runuser','/usr/sbin/rsyslogd','/usr/sbin/logrotate','/usr/bin/curl'):
        if not os.access(command,os.X_OK):
            raise ValueError('required trusted command is unavailable: '+command)
    base.command(['/usr/bin/findmnt','--noheadings','--types','tmpfs','--mountpoint','/run'])
    plan['mount'] = mount_identity(plan['config'])
    old = {}
    for path in sorted(BACKUPS.glob('network-storage-*/manifest.json')):
        if (path.parent/'complete').is_file() and not (path.parent/'rolled-back').exists():
            previous = json.loads(path.read_text())
            old.update({r['destination']:r['sha256'] for r in previous['files']})
    for row in plan['files']:
        info = base.regular_info(row['destination'])
        if info and info['sha256'] not in (row['sha256'],row['head_sha256'],INITIAL.get(row['destination']),old.get(row['destination'])):
            raise ValueError('unreviewed deployed difference: '+row['destination'])
        row['before'] = info
    # A missing config after a successful migration is a repair/update, never
    # permission to replace the flash database with the frozen SD copy.
    plan['migration'] = not CONFIG.exists() and not old
    plan['frontend_before']={name:base.link_info(base.FRONTEND/name) for name in ('current','previous')}
    if plan['frontend_before']['current'] is None:
        raise ValueError('existing frontend release required')
    plan['frontend_before']['tree_sha256']=base.tree_digest(base.FRONTEND/plan['frontend_before']['current'])
    plan['services_before'] = {name:base.service_state(name) for name in SERVICES}
    plan['backup_conf_before'] = base.regular_info(BACKUP_CONF)
    if plan['backup_conf_before'] is None:
        raise ValueError('existing backup configuration is unavailable')
    plan['backup_conf_sha256'] = base.sha(patch_exclusions(BACKUP_CONF.read_text()).encode())
    plan['source_log_bytes'] = sum(p.stat().st_size for p in OLD_LOGS.iterdir() if p.is_file() and not p.is_symlink()) if plan['migration'] else 0
    plan['source_database_bytes'] = OLD_DB.stat().st_size if plan['migration'] else 0
    fs = os.statvfs(plan['config']['mountpoint'])
    plan['flash_free_bytes'] = fs.f_bavail*fs.f_frsize
    if plan['flash_free_bytes'] < plan['source_database_bytes'] + plan['source_log_bytes'] + 512*1024*1024:
        raise ValueError('insufficient verified flash headroom')
    return plan


def verify(plan):
    base.command(['/usr/bin/findmnt','--noheadings','--types','tmpfs','--mountpoint','/run'])
    if mount_identity(plan['config']) != plan['mount']:
        raise ValueError('flash identity changed since check')
    for row in plan['files']:
        if base.regular_info(row['destination']) != row['before']:
            raise ValueError('managed file changed after check: '+row['destination'])
    if base.regular_info(BACKUP_CONF) != plan['backup_conf_before']:
        raise ValueError('backup configuration changed after check')
    if {name:base.service_state(name) for name in SERVICES} != plan['services_before']:
        raise ValueError('service state changed after check')
    before=plan['frontend_before']
    for name in ('current','previous'):
        if base.link_info(base.FRONTEND/name)!=before[name]:
            raise ValueError('frontend changed after check')
    if base.tree_digest(base.FRONTEND/before['current'])!=before['tree_sha256']:
        raise ValueError('frontend contents changed after check')


def restore_code(plan, root):
    for index,row in enumerate(plan['files']):
        target = Path(row['destination'])
        if row['before'] is None:
            if target.exists():
                if (target.is_symlink() or not target.is_file() or target.parent.resolve()!=target.parent or os.path.ismount(target)):
                    raise ValueError('unexpected rollback file')
                target.unlink()
        else:
            base.atomic_file(root/f'before-{index}',target,row['before'])
    base.atomic_file(root/'backup-conf-before',BACKUP_CONF,plan['backup_conf_before'])
    for name in ('current','previous'):
        value=plan['frontend_before'][name]
        if value is None:
            if (base.FRONTEND/name).is_symlink():(base.FRONTEND/name).unlink()
        else:
            base.atomic_link(value,base.FRONTEND/name)
    base.systemctl('daemon-reload')
    for name in ('rsyslog','network-flight-recorder','van-dashboard','van-dashboard-preview'):
        base.systemctl('restart' if plan['services_before'][name]['active'] else 'stop',name)


def migrate_data(plan, flash):
    temporary = 'events.sqlite3.migration-'+plan['release']
    flash.guard()
    descriptor = os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o640,dir_fd=flash.fd)
    os.close(descriptor)
    destination = Path(f'/proc/self/fd/{flash.fd}')/temporary
    counts = database_snapshot(OLD_DB,destination)
    checksum = base.digest(destination)
    flash.guard()
    try:
        os.stat('events.sqlite3',dir_fd=flash.fd,follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise ValueError('flash database already exists; refusing initial migration overwrite')
    os.rename(temporary,'events.sqlite3',src_dir_fd=flash.fd,dst_dir_fd=flash.fd)
    log_fd = flash.directory('openwrt')
    logs = []
    try:
        for source in sorted(OLD_LOGS.iterdir()):
            flash.guard()
            if source.is_symlink() or not source.is_file():
                raise ValueError('unexpected retired log entry')
            logs.append(dict(name=source.name,bytes=source.stat().st_size,sha256=copy_bytes(source,log_fd,source.name)))
    finally:
        os.close(log_fd)
    return dict(database_sha256=checksum,counts=counts,logs=logs)


def apply(plan, stage):
    import pwd
    validate_plan(plan)
    verify(plan)
    stage = Path(stage)
    if not re.fullmatch(r'/tmp/network-storage-stage\.[A-Za-z0-9_]+',str(stage)) or stage.is_symlink():
        raise ValueError('unsafe staging directory')
    for index,row in enumerate(plan['files']):
        source = stage/str(index)
        if base.digest(source)!=row['sha256']:
            raise ValueError('staged checksum mismatch')
        if row['source'].endswith('.py'):
            compile(source.read_text(),row['source'],'exec')
    for index,row in enumerate(plan['frontend_files']):
        if base.digest(stage/f'frontend-{index}')!=row['sha256']:
            raise ValueError('frontend staged checksum mismatch')
    receiver = next(i for i,r in enumerate(plan['files']) if r['destination'].endswith('30-openwrt-dendelion.conf'))
    base.command(['/usr/sbin/rsyslogd','-N1','-f',str(stage/str(receiver))])
    root = BACKUPS/plan['release']
    root.mkdir(parents=True,mode=0o700)
    os.chmod(root.parent,0o700)
    for index,row in enumerate(plan['files']):
        if row['before']:
            shutil.copyfile(row['destination'],root/f'before-{index}')
            if base.digest(root/f'before-{index}')!=row['before']['sha256']:
                raise ValueError('deployment rollback snapshot checksum mismatch')
    shutil.copyfile(BACKUP_CONF,root/'backup-conf-before')
    if base.digest(root/'backup-conf-before')!=plan['backup_conf_before']['sha256']:
        raise ValueError('backup configuration snapshot checksum mismatch')
    (root/'manifest.json').write_text(json.dumps(plan,indent=2)+'\n')
    verify(plan)
    flash = None
    try:
        base.systemctl('stop','network-flight-recorder')
        base.systemctl('stop','rsyslog')
        flash = FlashRoot(plan['config'],create=plan['migration'])
        if plan['migration']:
            plan['data_migration'] = migrate_data(plan,flash)
            (root/'manifest.json').write_text(json.dumps(plan,indent=2)+'\n')
        pi = pwd.getpwnam('pi')
        for index,row in enumerate(plan['files']):
            metadata = row['before'] or dict(uid=pi.pw_uid if row['destination'].startswith('/home/pi/') else 0,
                                             gid=pi.pw_gid if row['destination'].startswith('/home/pi/') else 0,
                                             mode=0o755 if row['destination'].endswith(('network_flight_recorder.py','vanpi-rotate-network-log','setup_openwrt_logging.sh')) else 0o644)
            base.atomic_file(stage/str(index),row['destination'],metadata)
        patched = root/'backup-conf-patched'
        patched.write_text(patch_exclusions((root/'backup-conf-before').read_text()))
        if base.digest(patched)!=plan['backup_conf_sha256']:
            raise ValueError('backup exclusion patch differs from checked plan')
        base.command(['/bin/bash','-n',str(patched)])
        base.atomic_file(patched,BACKUP_CONF,plan['backup_conf_before'])
        base.command(['/usr/bin/systemd-tmpfiles','--create','/etc/tmpfiles.d/vanpi-network.conf'])
        base.command(['/usr/sbin/rsyslogd','-N1'])
        base.command(['/usr/sbin/logrotate','--debug','/etc/logrotate.d/openwrt-dendelion'])
        frontend=base.FRONTEND/'releases'/plan['release']
        frontend.mkdir(mode=0o755)
        for index,row in enumerate(plan['frontend_files']):
            target=frontend/row['relative'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(stage/f'frontend-{index}',target)
        shutil.copyfile(base.FRONTEND/plan['frontend_before']['current']/'react_dashboard_preview.py',frontend/'react_dashboard_preview.py')
        (frontend/'BUILD_ID').write_text(plan['release']+'\n')
        for path in [frontend,*frontend.rglob('*')]:path.chmod(0o755 if path.is_dir() else 0o644)
        plan['installed_tree_sha256']=base.tree_digest(frontend)
        (root/'manifest.json').write_text(json.dumps(plan,indent=2)+'\n')
        base.atomic_link(plan['frontend_before']['current'],base.FRONTEND/'previous')
        base.atomic_link('releases/'+plan['release'],base.FRONTEND/'current')
        base.systemctl('daemon-reload')
        base.systemctl('restart','rsyslog')
        base.systemctl('restart','network-flight-recorder')
        if plan['services_before']['van-dashboard']['active']:
            base.systemctl('restart','van-dashboard')
        if plan['services_before']['van-dashboard-preview']['active']:
            base.systemctl('restart','van-dashboard-preview')
        readiness_started = time.time()
        for _ in range(45):
            if all(base.service_state(name)['active'] for name in ('rsyslog','network-flight-recorder')):
                result = base.command(['/usr/bin/curl','-fsS','--max-time','4','http://127.0.0.1:8788/api/network-history?hours=1'],False)
                status_path=Path(plan['config']['runtime_root'])/'storage-status.json'
                if result.returncode==0 and json.loads(result.stdout).get('ok') is True and status_path.is_file():
                    status=json.loads(status_path.read_text())
                    if (status.get('mode')=='flash' and status.get('active_database')==str(Path(plan['config']['flash_root'])/'events.sqlite3')
                            and status.get('checked_at',0)>=readiness_started):
                        flash.guard()
                        break
            time.sleep(1)
        else:
            raise RuntimeError('storage services/history endpoint failed readiness')
        (root/'complete').write_text(str(time.time())+'\n')
        return dict(ok=True,release=plan['release'],mode='flash',migration=plan['migration'],retired_sd_history='preserved and excluded from Borg')
    except Exception:
        # Migration never alters the original SD files. New flash data and any
        # runtime spool remain intact even if code installation is rolled back.
        base.command(['/usr/bin/systemctl','stop','network-flight-recorder'],False)
        base.command(['/usr/bin/systemctl','stop','rsyslog'],False)
        restore_code(plan,root)
        raise
    finally:
        if flash:
            flash.close()


def rollback(release):
    if not re.fullmatch(r'network-storage-[A-Za-z0-9._-]+',release):
        raise ValueError('invalid storage release')
    root = BACKUPS/release
    plan = json.loads((root/'manifest.json').read_text())
    validate_plan(plan)
    for row in plan['files']:
        info = base.regular_info(row['destination'])
        if info is None or info['sha256']!=row['sha256']:
            raise ValueError('managed files changed since deployment; refusing rollback')
    if base.regular_info(BACKUP_CONF)['sha256']!=plan['backup_conf_sha256']:
        raise ValueError('backup configuration changed since deployment; refusing rollback')
    if base.link_info(base.FRONTEND/'current')!='releases/'+release or base.tree_digest(base.FRONTEND/'releases'/release)!=plan['installed_tree_sha256']:
        raise ValueError('frontend changed since deployment; refusing rollback')
    flash = FlashRoot(plan['config'])
    try:
        base.systemctl('stop','network-flight-recorder')
        base.systemctl('stop','rsyslog')
        # Must drain fallback evidence before restoring old SD reporting.
        base.command(['/usr/sbin/runuser','-u','pi','--','/usr/bin/python3','/home/pi/scripts/network_flight_recorder.py',
                      '--storage-config',str(CONFIG),'storage-sync'])
        flash.guard()
        if plan['migration']:
            # Preserve the frozen old DB too, without copying FAT permissions.
            history_fd = flash.directory('rollback-history')
            try:
                copy_bytes(OLD_DB,history_fd,release+'-retired-sd.sqlite3')
            finally:
                os.close(history_fd)
            descriptor,temporary = tempfile.mkstemp(prefix='.network-rollback-',dir=OLD_DB.parent)
            os.close(descriptor)
            try:
                database_snapshot(Path(plan['config']['flash_root'])/'events.sqlite3',temporary)
                flash.guard()
                metadata = base.regular_info(OLD_DB)
                base.atomic_file(temporary,OLD_DB,metadata)
            finally:
                Path(temporary).unlink(missing_ok=True)
        restore_code(plan,root)
        (root/'rolled-back').write_text(str(time.time())+'\n')
        return dict(ok=True,rolled_back=release,new_history='consolidated and preserved',flash_history='retained')
    except Exception:
        # If synchronization cannot complete, leave new configuration and all
        # data intact; resume it rather than silently reviving stale SD history.
        for name in ('rsyslog','network-flight-recorder'):
            base.command(['/usr/bin/systemctl','restart',name],False)
        raise
    finally:
        flash.close()


def remote(target, action, request):
    base_source = (REPO/'pi/deploy_network_flight_recorder.py').read_text()
    base_source = base_source[:base_source.rindex("\nif __name__ == '__main__':")]
    source = Path(__file__).read_text()
    source = source[:source.rindex("\nif __name__ == '__main__':")]
    payload = "import types,sys\nbase=types.ModuleType('deploy_network_flight_recorder')\nbase.__file__='/tmp/pi/deploy_network_flight_recorder.py'\nsys.modules[base.__name__]=base\nexec("+repr(base_source)+",base.__dict__)\n"
    payload += source + '\nrequest='+repr(request)+'\n'
    call = {'check':'inspect(request)','apply':'apply(request["plan"],request["stage"])','rollback':'rollback(request["release"])'}[action]
    payload += 'print(json.dumps('+call+'))\n'
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',target,'sudo -n /usr/bin/python3 -'],input=payload,text=True,capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return json.loads(result.stdout)


def make_plan(target):
    rows=[]
    for source,destination in TARGETS.items():
        path=REPO/source
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing regular deployment source: '+source)
        head=subprocess.run(['git','-C',str(REPO),'show','HEAD:'+source],capture_output=True)
        rows.append(dict(source=source,destination=destination,sha256=base.digest(path),head_sha256=base.sha(head.stdout) if head.returncode==0 else None))
    dist=REPO/'pi/apps/van_dashboard/frontend/dist'
    frontend=[]
    for path in sorted(dist.rglob('*')):
        if path.is_symlink():raise ValueError('symlinked frontend artifact')
        if path.is_file():frontend.append(dict(relative=str(path.relative_to(dist)),sha256=base.digest(path)))
    return dict(schema_version=1,target=target,release='network-storage-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8],
                files=rows,frontend_files=frontend,config=json.loads((REPO/'pi/services/network-flight-recorder-storage.json').read_text()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',default='pi@vanpi.lan')
    sub=parser.add_subparsers(dest='action',required=True)
    for action in ('check','apply'):
        sub.add_parser(action).add_argument('--plan',required=True)
    sub.add_parser('rollback').add_argument('--release',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]*',args.target):
        parser.error('unsafe SSH target')
    if args.action=='check':
        path=Path(args.plan)
        if path.exists():
            raise ValueError('plan file already exists')
        plan=remote(args.target,'check',make_plan(args.target))
        path.write_text(json.dumps(plan,indent=2)+'\n');path.chmod(0o600)
        print(json.dumps(dict(ok=True,plan=str(path.absolute()),release=plan['release'],managed_files=len(plan['files']),mount=plan['mount'],
                              migration=plan['migration'],source_database_bytes=plan['source_database_bytes'],source_log_bytes=plan['source_log_bytes'],
                              backup_change=['/var/lib/vanpi-network','/var/log/openwrt']),indent=2))
    elif args.action=='apply':
        plan=json.loads(Path(args.plan).read_text());validate_plan(plan)
        current=make_plan(args.target)
        if (plan['target']!=args.target or plan['config']!=current['config'] or plan['frontend_files']!=current['frontend_files'] or
                [{k:v for k,v in r.items() if k!='before'} for r in plan['files']]!=current['files']):
            raise ValueError('local sources or target changed; create a fresh checked plan')
        stage=base.command(['ssh','-o','BatchMode=yes',args.target,'mktemp -d /tmp/network-storage-stage.XXXXXXXX']).stdout.strip()
        if not re.fullmatch(r'/tmp/network-storage-stage\.[A-Za-z0-9_]+',stage):
            raise ValueError('unexpected remote stage')
        with tempfile.TemporaryDirectory(prefix='network-storage-payload-') as temporary:
            for index,row in enumerate(plan['files']):
                shutil.copyfile(REPO/row['source'],Path(temporary)/str(index))
            for index,row in enumerate(plan['frontend_files']):
                shutil.copyfile(REPO/'pi/apps/van_dashboard/frontend/dist'/row['relative'],Path(temporary)/f'frontend-{index}')
            subprocess.run(['scp','-q',*[str(path) for path in sorted(Path(temporary).iterdir())],args.target+':'+stage+'/'],check=True)
        print(json.dumps(remote(args.target,'apply',dict(plan=plan,stage=stage)),indent=2))
    else:
        print(json.dumps(remote(args.target,'rollback',dict(release=args.release)),indent=2))


if __name__ == '__main__':
    main()
