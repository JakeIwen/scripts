"""Per-user launchd controller: persistent stops and KeepAlive restarts."""
import os
import plistlib
import time

from cli_runner import CliRunner, checked
from ownership_record import atomic_write, read_record
from panel_paths import DATA, LABEL, LOG, PLIST, PREPARED, PYTHON, RECORD, URL
from service_configuration import assert_plist_identity, configuration, parse_enabled, supervision_status
from service_http import ServiceHttp
from services_adapter import parse_print


class PanelService:
    def __init__(self, runner=None, http=None, plist=PLIST, prepared=PREPARED):
        self.runner = runner or CliRunner()
        self.http = http or ServiceHttp()
        self.plist, self.prepared = plist, prepared
        self.domain = f'gui/{os.getuid()}'
        self.target = f'{self.domain}/{LABEL}'

    def launchctl(self, *args, check=True):
        result = self.runner.run(('/bin/launchctl', *args), timeout=15)
        return checked(result) if check else result

    def job(self):
        result = self.launchctl('print', self.target, check=False)
        if result.returncode == 0:
            return {'loaded': True, **parse_print(result.stdout)}
        text = result.stdout + result.stderr
        if 'Could not find service' in text or 'Could not find domain' in text:
            return {'loaded': False, 'pid': None, 'state': 'not loaded'}
        raise RuntimeError('Cannot inspect the launchd job: ' + text.strip())

    def enabled(self):
        return parse_enabled(self.launchctl('print-disabled', self.domain).stdout)

    def assert_registration(self):
        assert_plist_identity(self.plist)
        if not self.plist.exists() and self.job()['loaded']:
            raise RuntimeError('A loaded job has no owned installed plist; refusing to control it')

    def prepare(self):
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        DATA.chmod(0o700)
        LOG.touch(mode=0o600, exist_ok=True)
        LOG.chmod(0o600)
        atomic_write(self.prepared, plistlib.dumps(configuration()))
        checked(self.runner.run(('/usr/bin/plutil', '-lint', str(self.prepared))))
        return {'prepared': str(self.prepared), 'destination': str(self.plist)}

    def status(self):
        assert_plist_identity(self.plist)
        job, health = self.job(), self.http.health()
        record = read_record(self.http.record_path)
        installed = self.plist.exists()
        configured = False
        if installed and self.prepared.exists():
            expected = configuration()
            configured = (plistlib.loads(self.plist.read_bytes()) == expected
                          and plistlib.loads(self.prepared.read_bytes()) == expected)
        result = supervision_status(installed, configured, self.enabled(), job['loaded'],
                                    job['pid'], health, record)
        return {**result, 'target': self.target, 'url': URL, 'log': str(LOG)}

    def wait_ready(self, previous=None, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.status()
            changed = not previous or (current['pid'] != previous['pid']
                                      and current['instance'] != previous['instance'])
            if current['healthy'] and changed:
                return current
            time.sleep(0.2)
        raise RuntimeError(f'Service did not become healthy within {timeout}s; inspect {LOG}')

    def wait_stopped(self, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.job()['loaded'] and not self.http.occupied():
                return
            time.sleep(0.2)
        raise RuntimeError('Service did not fully stop; no second instance was launched')

    def preflight(self):
        if os.getuid() == 0:
            raise RuntimeError('Run this controller as the login user, never as root')
        if not PYTHON.is_file() or not os.access(PYTHON, os.X_OK):
            raise RuntimeError('Panel interpreter is missing; run install.zsh')
        self.launchctl('print', self.domain)
        self.assert_registration()
        self.http.assert_owned_listener()

    def stop(self):
        self.assert_registration()
        self.http.assert_owned_listener()
        self.launchctl('disable', self.target)
        self.http.stop()
        if self.job()['loaded']:
            self.launchctl('bootout', self.target)
        self.wait_stopped()
        return {'stopped': True, 'enabled': False}

    def start(self):
        self.preflight()
        if not self.plist.exists():
            raise RuntimeError('The panel is not installed; run install.zsh')
        self.launchctl('enable', self.target)
        if not self.job()['loaded']:
            if self.http.health():
                self.http.stop()
                self.wait_stopped()
            self.launchctl('bootstrap', self.domain, str(self.plist))
        else:
            self.launchctl('kickstart', self.target)
        return self.wait_ready()

    def install(self):
        self.preflight()
        self.prepare()
        atomic_write(self.plist, self.prepared.read_bytes(), mode=0o644)
        if self.job()['loaded'] or self.http.health():
            self.stop()
        return self.start()

    def restart(self):
        self.assert_registration()
        self.http.assert_owned_listener()
        before = self.status()
        if not before['healthy']:
            raise RuntimeError('Start the installed, enabled service before restarting or verifying')
        self.http.stop()
        after = self.wait_ready(previous=before)
        return {'verified': True, 'automatic_restart': True, 'old_pid': before['pid'],
                'new_pid': after['pid'], 'new_instance': after['instance'], 'target': self.target}

    def uninstall(self):
        self.stop()
        self.plist.unlink(missing_ok=True)
        return {'uninstalled': True, 'data_retained': True}
