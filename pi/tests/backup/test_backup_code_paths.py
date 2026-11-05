"""Backup shell code finds its helpers beside itself, so a copied tree stays self-contained.

The installed tree is /home/pi/scripts, so resolving beside the sourced file
yields the former absolute paths there; a frozen offsite snapshot instead keeps
calling its own copies. Environment overrides still win.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
CONF_TREE = ('backup/backup_conf.sh', 'backup/backup_deploy_state.sh', 'backup/backup_locks.sh',
             'backup/backup_lock_io.sh', 'backup/backup_lock_registry.sh')
MOUNT_TREE = ('mount_disks.sh', 'disk_policy.sh', 'backup/stale_mount_recovery.sh')
SETTINGS = Path('/etc/vanpi-backup-settings.json')


@unittest.skipUnless(sys.platform.startswith('linux'), 'backup shell code requires Bash 4 (the Pi runs 5.2)')
class BackupCodePathTests(unittest.TestCase):
    def copy_tree(self, relatives):
        tree = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'scripts'
        for relative in relatives:
            destination = tree / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SCRIPTS / relative, destination)
        return tree

    def source(self, script, *arguments, **environment):
        env = {key: value for key, value in os.environ.items() if not key.startswith('VANPI_BACKUP_')}
        env.update(environment)
        return subprocess.run(['/bin/bash', '-c', script, 'test', *map(str, arguments)], env=env,
                              capture_output=True, text=True, timeout=20)

    def test_backup_conf_helpers_resolve_beside_the_sourced_copy(self):
        tree = self.copy_tree(CONF_TREE)
        records = tree.parent / 'settings-helper'
        # Runs only while the settings file exists, as on the Pi.
        (tree / 'backup/backup_settings_control.py').write_text(
            f'import pathlib\npathlib.Path({str(records)!r}).write_text(__file__)\n')
        # A marker kind proves the copy's registry (via its backup_locks.sh) was sourced.
        registry = tree / 'backup/backup_lock_registry.sh'
        registry.write_text(registry.read_text().replace('BACKUP_JOB_KINDS=(', 'BACKUP_JOB_KINDS=(copied '))
        script = '. "$1"; printf "%s\\n" "$BACKUP_PRIORITY_TOOL" "${BACKUP_JOB_KINDS[0]}"'
        for root, kind in ((tree, 'copied'), (SCRIPTS, 'borg')):
            result = self.source(script, root / 'backup/backup_conf.sh')
            self.assertEqual(result.stdout.splitlines(),
                             [str(root / 'backup/backup_priority_control.py'), kind], result.stderr)
        result = self.source(script, tree / 'backup/backup_conf.sh', VANPI_BACKUP_PRIORITY_TOOL='/fixed/tool.py')
        self.assertEqual(result.stdout.splitlines()[0], '/fixed/tool.py')
        if SETTINGS.exists() or SETTINGS.is_symlink():
            self.assertEqual(records.read_text(), str(tree / 'backup/backup_settings_control.py'))
        else:
            self.assertFalse(records.exists())

    def test_mount_library_sources_fix_hfs_beside_itself(self):
        tree = self.copy_tree(MOUNT_TREE)
        marker = tree.parent / 'fix-hfs'
        (tree / 'fix_hfs_fs.sh').write_text(f'printf "%s\\n" "${{BASH_SOURCE[0]}}" > {marker}\n')
        result = self.source('MOUNT_DISKS_LIBRARY_ONLY=1; . "$1" && md_fix_hfs_mounts', tree / 'mount_disks.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(marker.read_text().strip(), str(tree / 'fix_hfs_fs.sh'))
        (tree / 'fix_hfs_fs.sh').unlink()
        result = self.source('MOUNT_DISKS_LIBRARY_ONLY=1; . "$1" && md_fix_hfs_mounts', tree / 'mount_disks.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f'cannot read {tree}/fix_hfs_fs.sh', result.stderr)

    def test_abort_helper_sources_the_configuration_beside_itself(self):
        tree = self.copy_tree((*CONF_TREE, 'backup/abort_backup.sh'))
        (tree / 'backup/backup_settings_control.py').write_text('')
        # Only the copy's registry knows this label, so accepting it proves which conf was sourced.
        registry = tree / 'backup/backup_lock_registry.sh'
        registry.write_text(registry.read_text().replace('BACKUP_DISK_LABELS=(', 'BACKUP_DISK_LABELS=(copied '))
        absent = tree.parent / 'absent'
        isolated = {'VANPI_BACKUP_LOCK_DIR': str(absent / 'registry'), 'VANPI_BACKUP_JOB_LOCK': str(absent / 'legacy')}
        abort = tree / 'backup/abort_backup.sh'
        result = self.source('"$1" copied', abort, **isolated)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        for conf in (SCRIPTS / 'backup/backup_conf.sh', tree.parent / 'missing.sh'):
            result = self.source('"$1" copied', abort, ABORT_BACKUP_CONF=str(conf), **isolated)
            self.assertNotEqual(result.returncode, 0, conf)
        self.assertFalse(absent.exists())


class BackupSettingsPathTests(unittest.TestCase):
    def test_settings_read_the_configuration_beside_their_module(self):
        import backup_settings
        self.assertEqual(backup_settings.PATHS.local, SCRIPTS / 'backup/backup_conf.sh')


if __name__ == '__main__':
    unittest.main()
