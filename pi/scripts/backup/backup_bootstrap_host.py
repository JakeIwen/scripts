#!/usr/bin/env python3
"""Explicit, separately verified bootstrap maintenance; never a cohort updater.

Installed outside cohort rollback with its recovery dispatcher. Production use
is root-only. --root rebases filesystem paths for isolated image/fixture tests;
command adapters are inherited only when explicitly supplied in the environment.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import pwd
import grp
import subprocess
import sys
import uuid

import backup_bootstrap_files as files
import backup_bootstrap_plan as plan
import backup_locks as locks


class Bootstrap:
    def __init__(self, root='/'):
        self.root = Path(root)
        if not self.root.is_absolute() or (self.root == Path('/') and os.geteuid() != 0):
            raise ValueError('Bootstrap requires root; --root must be an absolute fixture root.')
        self.state = Path(os.environ.get('VANPI_BACKUP_DEPLOY_STATE_DIR', str(self.at(plan.STATE_DIR))))
        self.stable = Path(os.environ.get('VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR', str(self.at(plan.BOOTSTRAP_DIR))))
        self.cron = self.at(plan.CRON_PATH)
        self.maintenance = self.state / 'maintenance'
        self.receipt = self.state / 'bootstrap.json'
        self.bundle = self.state / 'bootstrap-inputs'
        self.ready = self.state / 'bootstrap-ready.json'
        self.publishing = self.state / 'bootstrap-publishing'
        self.cron_storage = Path(os.environ.get('VANPI_BOOTSTRAP_CRON_STORAGE', str(self.at(plan.CRON_STORAGE))))
        self.sync_tool = os.environ.get('VANPI_BOOTSTRAP_SYNC', '/usr/bin/sync')
        self.crontab = os.environ.get('VANPI_BOOTSTRAP_CRONTAB', '/usr/bin/crontab')
        self.systemctl = os.environ.get('VANPI_BOOTSTRAP_SYSTEMCTL', '/usr/bin/systemctl')
        self.busctl = os.environ.get('VANPI_BOOTSTRAP_BUSCTL', '/usr/bin/busctl')

    def at(self, path):
        return self.root / path.lstrip('/')

    def live(self, name):
        return self.at('/home/pi/scripts/backup/' + name)

    def command(self, *args, input=None):
        return subprocess.run(args, input=input, capture_output=True, text=True, check=True, timeout=30).stdout

    def crontab_read(self, user):
        result = subprocess.run([self.crontab, '-u', user, '-l'], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout
        if result.returncode == 1 and 'no crontab for ' + user in result.stderr:
            return ''
        raise RuntimeError('Cannot read ' + user + ' crontab: ' + result.stderr)

    @staticmethod
    def held_cron(text):
        output = []
        for line in text.splitlines(keepends=True):
            active = line.strip() and not line.lstrip().startswith('#') and not line.lstrip().startswith(('scripts=', 'cronlog='))
            matches = any('/' + name in line for name in plan.SCHEDULED_JOBS)
            # Unknown direct backup commands require review rather than assuming
            # the standard hourly window is the host's only admission route.
            if active and '/backup/' in line and not matches and 'backup_watchdog.sh' not in line:
                raise ValueError('Unrecognized backup cron entry; review the private crontab before continuing')
            output.append(plan.HOLD_PREFIX + line if active and matches else line)
        return ''.join(output)

    def set_cron(self, user, expected, updated):
        if self.crontab_read(user) != expected:
            raise ValueError(user + ' crontab changed; refusing to overwrite it')
        self.command(self.crontab, '-u', user, '-', input=updated)
        if self.crontab_read(user) != updated:
            raise ValueError('Could not verify ' + user + ' crontab')

    def dropin(self, service):
        return self.at('/etc/systemd/system/' + service + '.d/backup-deploy-bootstrap.conf')

    def dropin_bytes(self):
        return ('[Unit]\nConditionPathExists=!' + str(self.maintenance) + '\n').encode()

    def inspect_inputs(self, source):
        proposed = json.loads(files.read(source / 'plan.json'))
        if (proposed.get('version') != 1 or len(proposed['files']) != len(plan.GUARDS)
                or {r['name'] for r in proposed['files']} != set(plan.GUARDS)
                or set(proposed['stable']) != set(plan.STABLE_FILES)
                or set(proposed['checks']) != set(plan.GUARDS)):
            raise ValueError('Unsupported bootstrap plan')
        payload = {}
        expected = {r['name']: r['after'] for r in proposed['files']}
        expected.update({'stable-' + name: value for name, value in proposed['stable'].items()})
        for name, value in expected.items():
            payload[name] = files.read(source / name)
            if plan.digest(payload[name]) != value:
                raise ValueError('Staged bootstrap source changed: ' + name)
        self.verify_guards(proposed, source)
        return proposed, payload

    def check_predecessor(self, proposed, resuming):
        for record in proposed['files']:
            target = self.live(record['name'])
            if target.parent.is_symlink() or not target.parent.is_dir():
                raise ValueError('Unsafe bootstrap destination directory: ' + str(target.parent))
            allowed = (record['before'], record['after']) if resuming else (record['before'],)
            if self.current_hash(target) not in allowed:
                raise ValueError('Predecessor drift: ' + record['name'])

    def inspect_bootstrap(self, source):
        # All foreseeable refusals precede maintenance/receipt creation. Repeat
        # under the fence too: this preflight is not the admission boundary.
        proposed, payload = self.inspect_inputs(source)
        files.directory(self.state.parent, 0o755, create=False)
        if self.state.exists() or self.state.is_symlink():
            files.directory(self.state, 0o755, create=False)
        if os.path.lexists(self.state / 'pending'):
            raise ValueError('Recover the pending cohort before bootstrap maintenance')
        recorded = files.read_json(self.receipt) if os.path.lexists(self.receipt) else None
        if recorded is None and any(os.path.lexists(path) for path in (self.maintenance, self.publishing, self.ready)):
            raise ValueError('Unrecorded bootstrap intent requires inspection; refusing to invent scheduling originals')
        if recorded is not None and recorded['plan'] != proposed:
            raise ValueError('Different bootstrap plan; abandon the original only if publication has not started')
        self.check_predecessor(proposed, resuming=recorded is not None)
        cron = {user: self.crontab_read(user) for user in ('root', 'pi')}
        for user, actual in cron.items():
            original = recorded['cron'][user] if recorded is not None else actual
            if recorded is None and plan.HOLD_PREFIX in actual:
                raise ValueError('Unrecorded bootstrap cron holds require inspection')
            if actual not in (original, self.held_cron(original)):
                raise ValueError(user + ' crontab drifted; retain holds until reviewed')
        if not self.cron_storage.is_dir() or self.cron_storage.is_symlink():
            raise ValueError('Cannot identify cron persistence storage: ' + str(self.cron_storage))
        files.directory(self.stable.parent, 0o755, create=False)
        files.directory(self.cron.parent, 0o755, create=False)
        if self.stable.exists() or self.stable.is_symlink():
            files.directory(self.stable, 0o755, create=False)
            for name, expected in proposed['stable'].items():
                target = self.stable / name
                if os.path.lexists(target) and plan.digest(files.read(target, private=True)) != expected:
                    raise ValueError('Foreign bootstrap runtime: ' + name)
            recover = self.stable / 'recover'
            if os.path.lexists(recover) and files.read(recover, private=True) != self.recover_bytes():
                raise ValueError('Foreign recovery dispatcher')
        if os.path.lexists(self.cron) and files.read(self.cron, private=True) != self.cron_bytes():
            raise ValueError('Foreign recovery cron file')
        self.guard_owner()
        for service in plan.SERVICES:
            target = self.dropin(service)
            files.directory(target.parent.parent, 0o755, create=False)
            if target.parent.exists() or target.parent.is_symlink():
                files.directory(target.parent, 0o755, create=False)
            if os.path.lexists(target) and files.read(target, private=True) != self.dropin_bytes():
                raise ValueError('Foreign bootstrap drop-in: ' + str(target))
            self.service_conditions(service)
        return recorded or {'plan': proposed, 'cron': cron}, payload

    def save_inputs(self, recorded, payload):
        files.directory(self.bundle)
        for name, data in payload.items():
            files.write(self.bundle / name, data)
        # Scheduling originals are durable before the maintenance marker or a
        # single crontab change, so a prepublication attempt can be abandoned.
        files.write_json(self.receipt, recorded)

    def flush_crontabs(self):
        # crontab(1) owns its file names and rename protocol. syncfs on its actual
        # Linux spool flushes both file data and renamed directory entries.
        self.command(self.sync_tool, '-f', str(self.cron_storage))

    def hold_schedules(self, recorded):
        for user, original in recorded['cron'].items():
            held = self.held_cron(original)
            actual = self.crontab_read(user)
            if actual == original:
                self.set_cron(user, original, held)
            elif actual != held:
                raise ValueError(user + ' crontab drifted; holds remain until reviewed')
        for service in plan.SERVICES:
            target = self.dropin(service)
            files.directory(target.parent, 0o755)
            if target.exists() and files.read(target, private=True) != self.dropin_bytes():
                raise ValueError('Foreign bootstrap drop-in: ' + str(target))
            files.write(target, self.dropin_bytes(), 0o644)
        self.flush_crontabs()
        self.command(self.systemctl, 'daemon-reload')
        for service in plan.SERVICES:
            if self.command(self.systemctl, 'show', service, '-p', 'NeedDaemonReload', '--value').strip() != 'no':
                raise ValueError('Bootstrap condition is not loaded: ' + service)
            self.check_condition(service)

    def service_conditions(self, service):
        # show loads the unit metadata without starting it; GetUnit alone can
        # fail for a valid but not-yet-loaded service before the first reload.
        if self.command(self.systemctl, 'show', service, '-p', 'LoadState', '--value').strip() != 'loaded':
            raise ValueError('Bootstrap service is not loadable: ' + service)
        manager = 'org.freedesktop.systemd1'
        unit = json.loads(self.command(self.busctl, '--json=short', 'call', manager,
                                      '/org/freedesktop/systemd1', manager + '.Manager',
                                      'GetUnit', 's', service))
        if unit.get('type') != 'o' or len(unit.get('data', [])) != 1:
            raise ValueError('Cannot resolve loaded bootstrap unit: ' + service)
        condition = json.loads(self.command(self.busctl, '--json=short', 'get-property', manager,
                                           unit['data'][0], manager + '.Unit', 'Conditions'))
        if condition.get('type') != 'a(sbbsi)' or not isinstance(condition.get('data'), list):
            raise ValueError('Cannot inspect effective conditions: ' + service)
        return condition['data']

    def check_condition(self, service):
        # A later drop-in may reset our condition or turn it into an OR trigger.
        required = ['ConditionPathExists', False, True, str(self.maintenance)]
        if not any(isinstance(row, list) and len(row) == 5 and row[:4] == required
                   for row in self.service_conditions(service)):
            raise ValueError('Bootstrap maintenance condition is not effective: ' + service)

    @contextmanager
    def fence(self):
        fd = locks.open_lock(locks.legacy_lock(), legacy=True)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, (str(os.getpid()) + '\n').encode())
            yield
        finally:
            os.close(fd)

    def current_hash(self, path):
        try:
            return plan.digest(files.read(path))
        except FileNotFoundError:
            return 'absent'

    @staticmethod
    def guard_owner():
        return (pwd.getpwnam(os.environ.get('VANPI_BOOTSTRAP_PI_USER', 'pi')).pw_uid,
                grp.getgrnam(os.environ.get('VANPI_BOOTSTRAP_PI_GROUP', 'pi')).gr_gid)

    def publish_guards(self, proposed):
        # Check the complete set before the first edit, then recheck each rename.
        for record in proposed['files']:
            if self.current_hash(self.live(record['name'])) not in (record['before'], record['after']):
                raise ValueError('Predecessor drift: ' + record['name'])
        owner = self.guard_owner()
        ordered = sorted(proposed['files'], key=lambda r: r['name'] != 'backup_deploy_state.sh')
        for record in ordered:
            target = self.live(record['name'])
            if self.current_hash(target) == record['after']:
                continue
            if self.current_hash(target) != record['before']:
                raise ValueError('Predecessor changed during bootstrap: ' + record['name'])
            data = files.read(self.bundle / record['name'], private=True)
            if plan.digest(data) != record['after']:
                raise ValueError('Saved bootstrap input changed')
            mode = target.stat().st_mode & 0o777 if target.exists() else 0o750
            files.write(target, data, mode, owner)

    def recover_bytes(self):
        return ('#!/bin/bash\nset -eu\n. ' + str(self.stable / 'backup_deploy_trigger.sh') +
                '\nbackup_deploy_recover_pending\n').encode()

    def cron_bytes(self):
        return ('# Independent of backup_window.sh and cohort rollback.\n'
                '17 * * * * root /bin/bash ' + str(self.stable / 'recover') + '\n').encode()

    def install_dispatcher(self, proposed):
        files.directory(self.stable, 0o755)
        for name, expected in proposed['stable'].items():
            data = files.read(self.bundle / ('stable-' + name), private=True)
            if plan.digest(data) != expected:
                raise ValueError('Saved dispatcher input changed')
            files.write(self.stable / name, data, 0o644)
        files.write(self.stable / 'recover', self.recover_bytes(), 0o755)
        if self.cron.exists() and files.read(self.cron, private=True) != self.cron_bytes():
            raise ValueError('Foreign recovery cron file')
        files.write(self.cron, self.cron_bytes(), 0o644)

    @staticmethod
    def verify_guards(proposed, directory):
        for name, checks in proposed['checks'].items():
            text = files.read(directory / name).decode()
            if any(fragment not in text for fragment in checks):
                raise ValueError('Unsupported unguarded cohort file: ' + str(directory / name))

    def verify(self, stage=None, allow_maintenance=False):
        files.directory(self.state, 0o755, create=False)
        if os.path.lexists(self.state / 'pending'):
            raise ValueError('A cohort transaction is pending; recover it before checking bootstrap readiness')
        if os.path.lexists(self.maintenance) and not allow_maintenance:
            raise ValueError('Bootstrap maintenance is held; verify and explicitly release it first')
        proposed = files.read_json(self.ready)
        files.directory(self.stable, 0o755, create=False)
        for name, expected in proposed['stable'].items():
            if plan.digest(files.read(self.stable / name, private=True)) != expected:
                raise ValueError('Independent recovery dispatcher changed: ' + name)
        if files.read(self.stable / 'recover', private=True) != self.recover_bytes() or files.read(self.cron, private=True) != self.cron_bytes():
            raise ValueError('Independent recovery trigger is unavailable')
        for service in plan.SERVICES:
            if files.read(self.dropin(service), private=True) != self.dropin_bytes():
                raise ValueError('Bootstrap scheduling guard changed')
        self.verify_guards(proposed, self.at('/home/pi/scripts/backup'))
        if stage is not None:
            self.verify_guards(proposed, Path(stage) / 'pi/scripts/backup')
        return proposed

    def apply(self, source, confirmed):
        if not confirmed:
            raise ValueError('Confirm manual/dashboard starts held and old admission starters drained with --maintenance-confirmed')
        source = Path(source)
        self.inspect_bootstrap(source)
        with self.fence():
            recorded, payload = self.inspect_bootstrap(source)
            files.directory(self.state, 0o755)
            self.save_inputs(recorded, payload)
            files.write(self.maintenance, b'Bootstrap maintenance: verify, then explicitly release.\n', 0o644)
            self.hold_schedules(recorded)
            self.check_predecessor(recorded['plan'], resuming=True)
            # This conservative boundary makes prepublication abandonment
            # provable even after SIGKILL: no live guard/dispatcher write occurs
            # before it, and it is never removed by apply or release.
            files.write(self.publishing, b'Publication may have started; retain holds and resume the same bundle.\n')
            self.install_dispatcher(recorded['plan'])
            self.publish_guards(recorded['plan'])
            files.write_json(self.ready, recorded['plan'])
            self.verify(allow_maintenance=True)
        print('Bootstrap verified. Scheduling and manual-start holds remain; explicitly release when ready.')

    def restore_crontabs(self, recorded):
        for user, original in recorded['cron'].items():
            actual = self.crontab_read(user)
            if actual == self.held_cron(original):
                self.set_cron(user, actual, original)
            elif actual != original:
                raise ValueError(user + ' crontab drifted; maintenance remains held')
        self.flush_crontabs()

    def abandon(self, source, confirmed):
        if not confirmed:
            raise ValueError('Abandon requires --maintenance-confirmed; manual starts must remain held')
        proposed, _payload = self.inspect_inputs(Path(source))
        with self.fence():
            files.directory(self.state, 0o755, create=False)
            if any(os.path.lexists(path) for path in (self.publishing, self.ready, self.state / 'pending')):
                raise ValueError('Publication may have started; refusing abandon, retain holds and resume the same bundle')
            if os.path.lexists(self.stable) or os.path.lexists(self.cron):
                raise ValueError('Recovery publication exists; refusing to abandon an ambiguous bootstrap')
            self.check_predecessor(proposed, resuming=False)
            if os.path.lexists(self.receipt):
                recorded = files.read_json(self.receipt)
                if recorded['plan'] != proposed:
                    raise ValueError('Abandon must name the original recorded bundle')
                self.restore_crontabs(recorded)
            elif os.path.lexists(self.maintenance):
                raise ValueError('Unrecorded maintenance requires inspection; refusing to discard it')
            # No recursive cleanup and no rebind over an unresolved publication.
            # Keep evidence, but remove the active state pathname atomically only
            # after the actual cron spool has been flushed and restored.
            archive = self.state.with_name(self.state.name + '.abandoned.' + uuid.uuid4().hex)
            os.rename(self.state, archive)
            files.sync(self.state.parent)
        print('Unpublished bootstrap abandoned; scheduling restored. Evidence: ' + str(archive))

    def release(self, confirmed):
        if not confirmed:
            raise ValueError('Release requires --maintenance-confirmed; old shells cannot be revoked by this tool')
        with self.fence():
            self.verify(allow_maintenance=True)
            if os.path.lexists(self.state / 'pending'):
                raise ValueError('A cohort transaction is pending; maintenance remains held')
            self.restore_crontabs(files.read_json(self.receipt))
            if self.maintenance.exists():
                files.remove(self.maintenance)
        print('Bootstrap maintenance released; guarded admission and independent recovery remain installed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('VANPI_BOOTSTRAP_ROOT', '/'))
    parser.add_argument('--maintenance-confirmed', action='store_true')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('apply').add_argument('bundle', type=Path)
    sub.add_parser('abandon').add_argument('bundle', type=Path)
    verify = sub.add_parser('verify')
    verify.add_argument('--stage', type=Path)
    verify.add_argument('--held', action='store_true')
    sub.add_parser('release')
    args = parser.parse_args(argv)
    try:
        host = Bootstrap(args.root)
        if args.action == 'apply':
            host.apply(args.bundle, args.maintenance_confirmed)
        elif args.action == 'abandon':
            host.abandon(args.bundle, args.maintenance_confirmed)
        elif args.action == 'release':
            host.release(args.maintenance_confirmed)
        else:
            proposed = host.verify(args.stage, args.held)
            for record in proposed['files']:
                print('pi/scripts/backup/{name}:{before}:{after}'.format(**record))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
