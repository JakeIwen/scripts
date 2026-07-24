from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = REPOSITORY_ROOT / "pi" / "scripts" / "backup"


class BackupDeploymentTests(unittest.TestCase):
    def test_ubnt_snapshot_is_versioned_by_the_existing_borg_job(self):
        backup = (BACKUP_DIR / "pi_backup.sh").read_text(encoding="utf-8")
        ubnt_call = backup.index("/home/pi/scripts/backup/ubnt_backup.sh")
        borg_create = backup.index('log "borg create')

        self.assertLess(ubnt_call, borg_create)
        self.assertIn("last valid antenna snapshot", backup)

    def test_ubnt_snapshot_has_independent_freshness_monitoring(self):
        config = (BACKUP_DIR / "backup_conf.sh").read_text(encoding="utf-8")
        watchdog = (BACKUP_DIR / "backup_watchdog.sh").read_text(encoding="utf-8")

        self.assertIn('UBNT_SNAPSHOT_DIR="$SNAP_DIR/ubnt"', config)
        self.assertIn("UBNT_BACKUP_STALE_HOURS=72", config)
        self.assertIn('"$UBNT_SNAPSHOT_FILE"', watchdog)
        self.assertIn('"$UBNT_BACKUP_STAMP"', watchdog)

    def test_ubnt_export_is_validated_before_atomic_replacement(self):
        exporter = (BACKUP_DIR / "ubnt_backup.sh").read_text(encoding="utf-8")

        for requirement in (
            "/usr/bin/gzip -t",
            "snapshot contains an unsafe or unexpected path",
            "snapshot contains duplicate archive members",
            "persistent/profiles/system.cfg",
            "HostKeyAlgorithms=+ssh-rsa",
            '/bin/mv -f "$incoming" "$UBNT_SNAPSHOT_FILE"',
        ):
            self.assertIn(requirement, exporter)

        self.assertLess(
            exporter.index("/usr/bin/gzip -t"),
            exporter.index('/bin/mv -f "$incoming" "$UBNT_SNAPSHOT_FILE"'),
        )

    def test_ubnt_borg_layer_does_not_add_a_second_cron_job(self):
        crontab = (REPOSITORY_ROOT / "pi" / "crontab").read_text(encoding="utf-8")

        self.assertNotIn("ubnt_backup.sh", crontab)
        self.assertIn('"$scripts/backup/pi_backup.sh"', crontab)

    def test_backup_only_unmounts_bigboi_when_it_mounted_bigboi(self):
        backup = (BACKUP_DIR / "pi_backup.sh").read_text(encoding="utf-8")
        capture = "backup_mounted_here=$ENSURE_MOUNTED_DID_MOUNT"
        media_mount = 'ensure_mounted movingparts "$MEDIA_SRC"'
        guarded_cleanup = (
            'if [ "$backup_mounted_here" = 1 ]; then\n'
            '    if ! umount "$BACKUP_MNT"; then'
        )

        self.assertIn("ENSURE_MOUNTED_DID_MOUNT=0", backup)
        self.assertIn("ENSURE_MOUNTED_DID_MOUNT=1", backup)
        self.assertIn(capture, backup)
        self.assertLess(backup.index(capture), backup.index(media_mount))
        self.assertIn(guarded_cleanup, backup)
        self.assertIn("leaving pre-existing $BACKUP_MNT mount in place", backup)


if __name__ == "__main__":
    unittest.main()
