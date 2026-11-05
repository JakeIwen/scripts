import os
import grp
import pwd
import shutil
import sys
import random
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import backup_bootstrap_plan as bootstrap_plan
from pi.tests.backup.bootstrap_fixtures import BACKUP, seed_bootstrap

UPDATE_SERVICES = Path(__file__).resolve().parents[1] / "scripts" / "update_services.sh"

FAKE_SUDO = """#!/bin/sh
[ "$1" != -n ] || shift
exec "$@"
"""
# Logs every call; reports units active so changed units are restarted.
FAKE_SYSTEMCTL = """#!/bin/sh
echo "systemctl $*" >> "$FAKE_LOG"
exit 0
"""
FAKE_TMPFILES = """#!/bin/sh
echo "systemd-tmpfiles $*" >> "$FAKE_LOG"
"""


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


@unittest.skipUnless(sys.platform.startswith('linux'), 'receiver fence requires Linux flock/fdinfo')
class UpdateServicesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(self.cleanup_root)
        # The updater only accepts this exact staging-path shape.
        while True:
            self.stage = Path(f"/tmp/systemd-tmp.{random.randrange(10**8, 10**9)}")
            if not self.stage.exists():
                break
        self.addCleanup(self.cleanup_stage)

        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in (("sudo", FAKE_SUDO), ("systemctl", FAKE_SYSTEMCTL),
                           ("systemd-tmpfiles", FAKE_TMPFILES)):
            path = bin_dir / name
            path.write_text(body)
            path.chmod(0o755)
        self.log = self.root / "calls.log"
        self.host_root = self.root / 'live'
        self.live = {'services': self.host_root / 'etc/systemd/system',
                     'scripts': self.host_root / 'home/pi/scripts',
                     'tmpfiles': self.host_root / 'etc/tmpfiles.d'}
        for path in self.live.values():
            path.mkdir(parents=True)
        self.env = dict(
            os.environ,
            PATH=f"{bin_dir}:/usr/bin:/bin",
            FAKE_LOG=str(self.log),
            VANPI_BACKUP_DEPLOY_STATE_DIR=str(self.root / 'backup-deploy-state'),
            VANPI_BOOTSTRAP_ROOT=str(self.host_root),
            VANPI_BACKUP_JOB_LOCK=str(self.root / 'legacy'),
            VANPI_BACKUP_JOB_LOCK_OWNER=pwd.getpwuid(os.getuid()).pw_name,
            VANPI_BACKUP_JOB_LOCK_GROUP=grp.getgrgid(os.getgid()).gr_name,
            VANPI_BACKUP_JOB_LOCK_MODE='660',
            UPDATE_SERVICES_SUDO=str(bin_dir / 'sudo'),
            UPDATE_SERVICES_LIVE_SERVICES=str(self.live["services"]),
            UPDATE_SERVICES_LIVE_SCRIPTS=str(self.live["scripts"]),
            UPDATE_SERVICES_LIVE_TMPFILES=str(self.live["tmpfiles"]),
        )

        lock = Path(self.env['VANPI_BACKUP_JOB_LOCK'])
        lock.touch()
        lock.chmod(0o660)
        boundary = self.stage / '.backup-sync'
        boundary.mkdir(parents=True)
        for name in bootstrap_plan.SYNC_FILES:
            shutil.copyfile(BACKUP / name, boundary / name)

        # Staged tree. One new and one changed unit keep both unit arrays
        # non-empty, so the script also runs under macOS's bash 3.2.
        (self.stage / "services").mkdir(parents=True)
        (self.stage / "scripts" / "lib").mkdir(parents=True)
        (self.stage / "tmpfiles.d").mkdir()
        (self.stage / "services" / "new.service").write_text("[Service]\nExecStart=/bin/true\n")
        (self.stage / "services" / "changed.service").write_text(
            "[Service]\nExecStart=/home/pi/scripts/tool.sh\n")
        (self.stage / "tmpfiles.d" / "x.conf").write_text("f /run/lock/x 0660 root pi -\n")
        for name in ("tool.sh", "helper.py"):
            staged = self.stage / "scripts" / name
            staged.write_text(f"# new {name}\n")
            staged.chmod(0o644)  # repository mode, as the sync stages it
        nested = self.stage / "scripts" / "lib" / "module.py"
        nested.write_text("# nested\n")
        nested.chmod(0o644)

        # Live tree from an earlier successful deployment.
        (self.live["services"] / "changed.service").write_text("[Service]\nExecStart=/bin/false\n")
        for name in ("tool.sh", "helper.py"):
            live = self.live["scripts"] / name
            live.write_text(f"# old {name}\n")
            live.chmod(0o770)
        self.unrelated = self.live["scripts"] / "unrelated.sh"
        self.unrelated.write_text("# not staged\n")
        self.unrelated.chmod(0o640)

    def cleanup_root(self):
        for path in self.root.rglob("*"):
            if path.is_dir() and not path.is_symlink():
                path.chmod(0o755)
        subprocess.run(["/bin/rm", "-rf", "--", str(self.root)], check=False)

    def cleanup_stage(self):
        if self.stage.exists():
            subprocess.run(["/bin/rm", "-rf", "--", str(self.stage)], check=False)

    def run_updater(self):
        return subprocess.run(["/bin/bash", str(UPDATE_SERVICES), str(self.stage)],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def bootstrap_ready(self):
        backup = self.live['scripts'] / 'backup'
        backup.mkdir(exist_ok=True)
        for name in bootstrap_plan.GUARDS:
            shutil.copyfile(BACKUP / name, backup / name)
        host = seed_bootstrap(self.root, self.host_root, self.env)
        (host.state / 'bootstrap-publishing').write_text('completed publication intent\n')
        return host

    def test_symlinked_stage_refuses_before_loading_staged_code(self):
        actual = self.root / 'actual-stage'
        self.stage.rename(actual)
        self.stage.symlink_to(actual)
        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsafe or incomplete', result.stderr)
        self.assertFalse(self.log.exists())
        self.assertTrue((actual / 'scripts/tool.sh').exists())

    def test_incomplete_state_refuses_receiver_before_any_live_write(self):
        state = Path(self.env['VANPI_BACKUP_DEPLOY_STATE_DIR'])
        for kind in ('directory', 'dangling-symlink'):
            with self.subTest(kind=kind):
                if kind == 'directory':
                    state.mkdir()
                else:
                    state.symlink_to(self.root / 'absent')
                result = self.run_updater()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Backup sync refused', result.stderr)
                self.assertFalse(self.log.exists())
                self.assertEqual((self.live['scripts'] / 'tool.sh').read_text(), '# old tool.sh\n')
                self.assertTrue(self.stage.exists())
                if kind == 'directory':
                    state.rmdir()
                else:
                    state.unlink()

    def test_healthy_bootstrap_updates_helper_without_touching_backup_or_directory_metadata(self):
        host = self.bootstrap_ready()
        backup = self.live['scripts'] / 'backup'
        backup.chmod(0o750)
        previous = {path: path.read_bytes() for path in backup.iterdir()}
        (self.stage / 'scripts/backup').mkdir()
        (self.stage / 'scripts/backup').chmod(0o777)
        helper = self.live['scripts'] / 'connectivity_status.py'
        helper.write_text('# old connectivity helper\n')
        shutil.copyfile(BACKUP.parent / helper.name, self.stage / 'scripts' / helper.name)
        (self.stage / 'tmpfiles.d/x.conf').unlink()  # all canonical/storage tmpfiles may be excluded
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(helper.read_bytes(), (BACKUP.parent / helper.name).read_bytes())
        self.assertEqual(previous, {path: path.read_bytes() for path in previous})
        self.assertEqual(mode(backup), 0o750)
        self.assertTrue((host.state / 'bootstrap-publishing').exists())

    def test_pending_maintenance_and_malformed_readiness_refuse_before_writes(self):
        host = self.bootstrap_ready()
        for name in ('maintenance', 'pending'):
            marker = host.state / name
            marker.write_text('held\n')
            marker.chmod(0o600)
            result = self.run_updater()
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self.log.exists())
            marker.unlink()
        host.ready.write_text('{}')
        self.assertNotEqual(self.run_updater().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_polluted_payload_and_parent_symlink_refuse_before_unrelated_writes(self):
        self.bootstrap_ready()
        for relative in ('scripts/backup/backup_conf.sh', 'scripts/mount_disks.sh',
                         'services/vanpi-icloud-backup.service', 'tmpfiles.d/vanpi-backup.conf'):
            with self.subTest(relative=relative):
                path = self.stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('polluted\n')
                result = self.run_updater()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Backup-owned staged overlap', result.stderr)
                self.assertFalse(self.log.exists())
                self.assertEqual((self.live['scripts'] / 'tool.sh').read_text(), '# old tool.sh\n')
                path.unlink()
        (self.stage / 'scripts/backup').rmdir()
        (self.stage / 'scripts/backup').symlink_to(self.root)
        self.assertNotEqual(self.run_updater().returncode, 0)
        self.assertFalse(self.log.exists())

    def test_cleanup_destination_newly_owned_by_plan_is_refused(self):
        self.bootstrap_ready()
        canonical = self.stage / '.backup-sync/backup_deploy_plan.sh'
        with canonical.open('a') as stream:
            stream.write("\nBACKUP_DEPLOY_PLAN+=('both|pi/scripts/setup_router_policy_trigger.sh|"
                         "/home/pi/scripts/setup_router_policy_trigger.sh|revision|pi:pi|750|new')\n")
        legacy = self.live['scripts'] / 'setup_router_policy_trigger.sh'
        legacy.write_text('protected cleanup target\n')
        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Backup-owned cleanup overlap', result.stderr)
        self.assertEqual(legacy.read_text(), 'protected cleanup target\n')
        self.assertFalse(self.log.exists())

    def test_nonbackup_alias_cannot_write_through_to_protected_code(self):
        self.bootstrap_ready()
        protected = self.live['scripts'] / 'backup/backup_conf.sh'
        before = protected.read_bytes()
        alias = self.live['scripts'] / 'tool.sh'
        alias.unlink()
        alias.symlink_to(protected)
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(protected.read_bytes(), before)
        self.assertFalse(alias.is_symlink())
        self.assertEqual(alias.read_text(), '# new tool.sh\n')

    def test_root_private_readiness_is_checked_with_privilege(self):
        if os.geteuid() == 0:
            self.skipTest('requires a separate unprivileged installer identity')
        private = self.root / 'root-state'
        private.mkdir()
        self.env['VANPI_BACKUP_DEPLOY_STATE_DIR'] = str(private / 'state')
        host = self.bootstrap_ready()
        paths = [private, host.stable, host.cron.parent, self.live['services']]
        self.addCleanup(subprocess.run, ['/usr/bin/sudo', '-n', '/usr/bin/chown', '-R',
                                        str(os.getuid()) + ':' + str(os.getgid()), str(self.root)], check=True)
        subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/chown', '-R', 'root:root', *map(str, paths)], check=True)
        subprocess.run(['/usr/bin/sudo', '-n', '/bin/chmod', '600', str(host.ready)], check=True)
        sudo = self.root / 'bin/sudo'
        sudo.write_text('#!/bin/sh\n[ "$1" != -n ] || shift\n'
                        'if [ "$1" = /usr/bin/python3 ]; then\n'
                        ' exec /usr/bin/sudo -n -E /usr/bin/env PATH="$PATH" "$@"\nfi\nexec "$@"\n')
        # Only inspect/refuse here: root-owned services are not writable by the
        # fixture installer, and no actual supervisor command may run.
        pending = host.state / 'pending'
        subprocess.run(['/usr/bin/sudo', '-n', '/usr/bin/touch', str(pending)], check=True)
        subprocess.run(['/usr/bin/sudo', '-n', '/bin/chmod', '600', str(pending)], check=True)
        subprocess.run(['/usr/bin/sudo', '-n', '/bin/chmod', '700', str(private)], check=True)
        self.assertFalse(os.path.lexists(host.state), 'fixture must hide even state existence from the installer')
        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('transaction is pending', result.stderr)
        self.assertFalse(self.log.exists())
        subprocess.run(['/usr/bin/sudo', '-n', '/bin/rm', str(pending)], check=True)
        subprocess.run(['/usr/bin/sudo', '-n', '/bin/chmod', '755', str(private)], check=True)
        checked = subprocess.run([str(sudo), '/usr/bin/python3', '-B',
                                  str(self.stage / '.backup-sync/backup_deploy_sync.py'), 'check'],
                                 env=self.env, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_successful_update_makes_only_staged_top_level_scripts_executable(self):
        result = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("tool.sh", "helper.py"):
            live = self.live["scripts"] / name
            self.assertEqual(live.read_text(), f"# new {name}\n")
            self.assertEqual(mode(live), 0o770, name)
        self.assertEqual(mode(self.live["scripts"] / "lib" / "module.py"), 0o644)
        self.assertEqual(mode(self.unrelated), 0o640)
        calls = self.log.read_text()
        self.assertIn("systemctl enable --now new.service", calls)
        self.assertIn("systemctl restart changed.service", calls)
        self.assertFalse(self.stage.exists(), "staging directory is cleaned up")

    def test_failed_copy_never_leaves_a_live_script_non_executable(self):
        # A live directory the deploying user cannot write makes cp -a fail
        # partway, as a root-owned directory does on the Pi.
        locked_stage = self.stage / "scripts" / "locked"
        locked_stage.mkdir()
        (locked_stage / "file.txt").write_text("new\n")
        locked_live = self.live["scripts"] / "locked"
        locked_live.mkdir()
        locked_live.chmod(0o555)

        result = self.run_updater()
        self.assertNotEqual(result.returncode, 0, "the copy failure must abort the update")
        self.assertNotIn("daemon-reload", self.log.read_text() if self.log.exists() else "")
        for name in ("tool.sh", "helper.py"):
            live = self.live["scripts"] / name
            self.assertTrue(mode(live) & stat.S_IXUSR,
                            f"{name} lost its exec bit: {oct(mode(live))}")


if __name__ == "__main__":
    unittest.main()
