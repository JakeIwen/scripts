"""Minimal, reviewed edits to a selected predecessor, never a replacement cohort.

A provider change outside these exact anchors is preserved byte-for-byte. A
changed anchor refuses staging; the operator must review a new transformation.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

STATE_DIR = '/var/lib/vanpi-backup-deploy'
BOOTSTRAP_DIR = '/usr/local/lib/vanpi-backup-deploy'
CRON_PATH = '/etc/cron.d/vanpi-backup-deploy'
CRON_STORAGE = '/var/spool/cron/crontabs'
MAINTENANCE = STATE_DIR + '/maintenance'
HOLD_PREFIX = '# vanpi-deploy-bootstrap-held '
SERVICES = ('vanpi-icloud-backup.service', 'vanpi-time-machine-icloud.service',
            'vanpi-icloud-backup-resume.service', 'vanpi-time-machine-icloud-resume.service')
# All code that introduces admission, plus the frozen offsite dependency list.
GUARDS = ('backup_conf.sh', 'backup_locks.sh', 'backup_locks.py', 'backup_admission.py',
          'offsite_backup.sh', 'offsite_config.py', 'clone_to_sd.sh', 'backup_deploy_state.sh')
STABLE_FILES = ('backup_bootstrap_host.py', 'backup_bootstrap_plan.py', 'backup_bootstrap_files.py',
                'backup_deploy_trigger.sh', 'backup_deploy_state.sh', 'backup_locks.py')
SYNC_FILES = STABLE_FILES + ('backup_deploy_plan.sh', 'backup_deploy_sync.py')
SYNC_READY = 'backup-sync-ready'
SYNC_RELEASE = 'backup-sync-release'
SCHEDULED_JOBS = ('backup_window.sh', 'pi_backup.sh', 'exfat_snapshot.sh', 'clone_now.sh',
                  'clone_to_sd.sh', 'hotspare_rotation.sh', 'icloud_backup.sh',
                  'time_machine_icloud.sh', 'offsite_backup.sh', 'openwrt_backup.sh')


def validate_plan(proposed):
    try:
        records, stable, checks = proposed['files'], proposed['stable'], proposed['checks']
        valid = (type(proposed['version']) is int and proposed['version'] == 1
                 and isinstance(proposed['revision'], str) and bool(proposed['revision'])
                 and isinstance(records, list) and len(records) == len(GUARDS)
                 and {record['name'] for record in records} == set(GUARDS)
                 and isinstance(stable, dict) and set(stable) == set(STABLE_FILES)
                 and isinstance(checks, dict) and set(checks) == set(GUARDS))
        hashes = list(stable.values()) + [record['after'] for record in records]
        hashes += [record['before'] for record in records if record['before'] != 'absent']
        valid = valid and all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) for value in hashes)
        valid = valid and all(isinstance(parts, list) and parts and
                              all(isinstance(part, str) and part for part in parts) for parts in checks.values())
    except (AttributeError, KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Unsupported bootstrap plan')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def shell_function(text, name):
    match = re.search(r'^' + re.escape(name) + r'\(\) \{.*?^\}', text, re.M | re.S)
    if not match:
        raise ValueError('Missing bootstrap template function: ' + name)
    return match.group() + '\n\n'


def replacements(current):
    """Source snippets are the runtime implementation, not another guard implementation."""
    locks = (current / 'backup_locks.sh').read_text()
    pylocks = (current / 'backup_locks.py').read_text()
    require = pylocks[pylocks.index('def require_legacy_hold('):pylocks.index('def require_job_lock(')]
    cli = pylocks[pylocks.index("if __name__ == '__main__':"):]
    admission = (current / 'backup_admission.py').read_text()
    new_admission = admission[admission.index('    legacy = validated_descriptor('):admission.index('        validated_descriptor(job_fd,')]
    return {
        'backup_conf.sh': [
            ('validate_job_lock_file() {', '. "$BACKUP_CONF_DIR/backup_deploy_state.sh" || return 1\nvalidate_job_lock_file() {'),
            ('  local metadata owner group mode inode extra\n', '  local metadata owner group mode inode extra\n  backup_deploy_refuse_jobs || return 1\n'),
            ('  "$JOB_LOCK_FLOCK" -n 9 || return 1\n', '  "$JOB_LOCK_FLOCK" -n 9 || return 1\n  backup_deploy_refuse_jobs || { exec 9<&-; return 1; }\n')],
        'backup_locks.sh': [
            ('acquire_backup_job() {', shell_function(locks, 'ensure_clone_admission') + 'acquire_backup_job() {'),
            ('  backup_lock_open fd "$path" backup_lock_validate || { exec 9>&-; return 1; }',
             '  backup_deploy_refuse_jobs || { exec 9<&-; return 1; }\n  backup_lock_open fd "$path" backup_lock_validate || { exec 9>&-; return 1; }')],
        'backup_locks.py': [('def require_job_lock(', require + 'def require_job_lock(')],
        'backup_admission.py': [
            ('import re\n', 'import re\nimport subprocess\n'),
            ('    return WorkerAdmission(kind, registry,\n        validated_descriptor(legacy_fd, locks.legacy_lock(), exclusive=False),\n', new_admission)],
        'offsite_backup.sh': [
            ("    refuse 'A restore, hotspare initialization, prepare-disk or backup deployment is active.'\n",
             "    refuse 'A restore, hotspare initialization, prepare-disk or backup deployment is active.'\n  backup_deploy_refuse_jobs || exit 1\n")],
        'offsite_config.py': [
            ("    'backup/abort_backup.sh', 'backup/backup_admission.py', 'backup/backup_conf.sh',\n",
             "    'backup/abort_backup.sh', 'backup/backup_admission.py', 'backup/backup_conf.sh',\n    'backup/backup_deploy_state.sh',\n")],
        'clone_to_sd.sh': [
            ('label=${1:?usage: /home/pi/scripts/backup/clone_to_sd.sh [--init] <label> [sdX]}\n',
             'label=${1:?usage: /home/pi/scripts/backup/clone_to_sd.sh [--init] <label> [sdX]}\nensure_clone_admission || exit 1\n')],
    }, cli


def patch(name, before, current):
    rules, cli = replacements(current)
    text = before.decode()
    if all(fragment in text for fragment in guard_checks(current)[name]):
        return before
    for old, new in rules[name]:
        if old == new:
            continue
        if new in text:
            continue
        if text.count(old) != 1:
            raise ValueError('Unsupported predecessor anchor in ' + name)
        text = text.replace(old, new, 1)
    if name == 'backup_locks.py' and cli not in text:
        if "if __name__ == '__main__':" in text:
            raise ValueError('Unsupported predecessor CLI in ' + name)
        text += '\n\n' + cli
    return text.encode()


def guard_checks(current):
    # Stable preflight checks the executed guard bodies at their admission sites,
    # not a version comment which could outlive a removed implementation.
    rules, cli = replacements(current)
    checks = {name: [new for old, new in pairs if old != new] for name, pairs in rules.items()}
    checks['backup_locks.py'].append(cli)
    checks['backup_conf.sh'] = [
        '. "$BACKUP_CONF_DIR/backup_deploy_state.sh" || return 1',
        '  backup_deploy_refuse_jobs || return 1\n',
        '  backup_deploy_refuse_jobs || { exec 9<&-; return 1; }']
    checks['backup_deploy_state.sh'] = ['backup_deploy_refuse_jobs() {', 'BACKUP_DEPLOY_MAINTENANCE=',
                                      'backup_deploy_pending || return 0']
    return checks


def build(repo, revision, output):
    repo, output = Path(repo), Path(output)
    current = repo / 'pi/scripts/backup'
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', '--verify', revision + '^{commit}'], text=True).strip()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    records = []
    for name in GUARDS:
        source = 'pi/scripts/backup/' + name
        exists = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', revision, '--', source], text=True)
        before = subprocess.check_output(['git', '-C', str(repo), 'show', revision + ':' + source]) if exists else None
        if name == 'backup_deploy_state.sh':
            after = (current / name).read_bytes()
        elif before is None:
            raise ValueError('Required predecessor missing: ' + source)
        else:
            after = patch(name, before, current)
        (output / name).write_bytes(after)
        records.append({'name': name, 'before': digest(before) if before is not None else 'absent', 'after': digest(after)})
    (output / 'runner').mkdir(mode=0o700)
    for name in STABLE_FILES:
        data = (current / name).read_bytes()
        (output / ('stable-' + name)).write_bytes(data)
        (output / 'runner' / name).write_bytes(data)
    plan = {'version': 1, 'revision': revision, 'files': records, 'checks': guard_checks(current),
            'stable': {name: digest((current / name).read_bytes()) for name in STABLE_FILES}}
    (output / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    return plan
