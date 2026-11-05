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

    def save_inputs(self, source):
        proposed = json.loads(files.read(source / 'plan.json'))
        if proposed.get('version') != 1 or {r['name'] for r in proposed['files']} != set(plan.GUARDS):
            raise ValueError('Unsupported bootstrap plan')
        if self.receipt.exists():
            recorded = files.read_json(self.receipt)
            if recorded['plan'] != proposed:
                raise ValueError('An existing bootstrap belongs to a different plan; retain holds and review it')
            return recorded
        files.directory(self.bundle)
        for record in proposed['files']:
            data = files.read(source / record['name'])
            if plan.digest(data) != record['after']:
                raise ValueError('Staged bootstrap source changed')
            files.write(self.bundle / record['name'], data)
        for name, expected in proposed['stable'].items():
            if name not in plan.STABLE_FILES:
                raise ValueError('Unknown stable bootstrap file')
            data = files.read(source / ('stable-' + name))
            if plan.digest(data) != expected:
                raise ValueError('Stable bootstrap source changed')
            files.write(self.bundle / ('stable-' + name), data)
        recorded = {'plan': proposed, 'cron': {user: self.crontab_read(user) for user in ('root', 'pi')}}
        # Both original crontabs are durable before either one is changed.
        for text in recorded['cron'].values():
            self.held_cron(text)
        files.write_json(self.receipt, recorded)
        return recorded

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
        self.command(self.systemctl, 'daemon-reload')
        for service in plan.SERVICES:
            if self.command(self.systemctl, 'show', service, '-p', 'NeedDaemonReload', '--value').strip() != 'no':
                raise ValueError('Bootstrap condition is not loaded: ' + service)
            self.check_condition(service)

    def check_condition(self, service):
        # `systemctl show Conditions` says [unprintable] on Bookworm. Read the
        # typed property: a later drop-in could reset or turn our condition into
        # an OR trigger even while our own file is present and reload succeeded.
        manager = 'org.freedesktop.systemd1'
        unit = json.loads(self.command(self.busctl, '--json=short', 'call', manager,
                                      '/org/freedesktop/systemd1', manager + '.Manager',
                                      'GetUnit', 's', service))
        if unit.get('type') != 'o' or len(unit.get('data', [])) != 1:
            raise ValueError('Cannot resolve loaded bootstrap unit: ' + service)
        condition = json.loads(self.command(self.busctl, '--json=short', 'get-property', manager,
                                           unit['data'][0], manager + '.Unit', 'Conditions'))
        required = ['ConditionPathExists', False, True, str(self.maintenance)]
        if condition.get('type') != 'a(sbbsi)' or not any(
                isinstance(row, list) and len(row) == 5 and row[:4] == required
                for row in condition.get('data', [])):
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

    def publish_guards(self, proposed):
        # Check the complete set before the first edit, then recheck each rename.
        for record in proposed['files']:
            if self.current_hash(self.live(record['name'])) not in (record['before'], record['after']):
                raise ValueError('Predecessor drift: ' + record['name'])
        owner = (pwd.getpwnam(os.environ.get('VANPI_BOOTSTRAP_PI_USER', 'pi')).pw_uid,
                 grp.getgrnam(os.environ.get('VANPI_BOOTSTRAP_PI_GROUP', 'pi')).gr_gid)
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
        if self.maintenance.exists() and not allow_maintenance:
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
        files.directory(self.state, 0o755)
        if (self.state / 'pending').exists():
            raise ValueError('Recover the pending cohort before bootstrap maintenance')
        files.write(self.maintenance, b'Bootstrap maintenance: verify, then explicitly release.\n', 0o644)
        recorded = self.save_inputs(Path(source))
        self.hold_schedules(recorded)
        with self.fence():
            self.install_dispatcher(recorded['plan'])
            self.publish_guards(recorded['plan'])
            files.write_json(self.ready, recorded['plan'])
            self.verify(allow_maintenance=True)
        print('Bootstrap verified. Scheduling and manual-start holds remain; explicitly release when ready.')

    def release(self, confirmed):
        if not confirmed:
            raise ValueError('Release requires --maintenance-confirmed; old shells cannot be revoked by this tool')
        with self.fence():
            self.verify(allow_maintenance=True)
            if (self.state / 'pending').exists():
                raise ValueError('A cohort transaction is pending; maintenance remains held')
            recorded = files.read_json(self.receipt)
            for user, original in recorded['cron'].items():
                actual = self.crontab_read(user)
                if actual == self.held_cron(original):
                    self.set_cron(user, actual, original)
                elif actual != original:
                    raise ValueError(user + ' crontab drifted; maintenance remains held')
            if self.maintenance.exists():
                files.remove(self.maintenance)
        print('Bootstrap maintenance released; guarded admission and independent recovery remain installed.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('VANPI_BOOTSTRAP_ROOT', '/'))
    parser.add_argument('--maintenance-confirmed', action='store_true')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('apply').add_argument('bundle', type=Path)
    verify = sub.add_parser('verify')
    verify.add_argument('--stage', type=Path)
    verify.add_argument('--held', action='store_true')
    sub.add_parser('release')
    args = parser.parse_args(argv)
    try:
        host = Bootstrap(args.root)
        if args.action == 'apply':
            host.apply(args.bundle, args.maintenance_confirmed)
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
