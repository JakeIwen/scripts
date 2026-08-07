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

    def test_backup_window_runs_independently_stamped_jobs_from_one_cron_entry(self):
        crontab = (REPOSITORY_ROOT / "pi" / "crontab").read_text(encoding="utf-8")
        runner = (BACKUP_DIR / "backup_window.sh").read_text(encoding="utf-8")

        self.assertNotIn("ubnt_backup.sh", crontab)
        self.assertIn('"$scripts/backup/backup_window.sh"', crontab)
        self.assertNotIn('"$scripts/backup/pi_backup.sh"', crontab)
        self.assertIn("pi_backup.sh exfat_snapshot.sh", runner)

    def test_exfat_snapshot_is_exact_label_hard_linked_and_independently_stamped(self):
        config = (BACKUP_DIR / "backup_conf.sh").read_text(encoding="utf-8")
        snapshot = (BACKUP_DIR / "exfat_snapshot.sh").read_text(encoding="utf-8")
        watchdog = (BACKUP_DIR / "backup_watchdog.sh").read_text(encoding="utf-8")

        self.assertIn('EXFAT_SNAPSHOT_ROOT="$EXFAT_SNAPSHOT_MNT/backups"', config)
        self.assertIn("EXFAT_SNAPSHOT_PREFIX=EXFAT512_", config)
        self.assertIn("--link-dest=$previous_path", snapshot)
        self.assertIn("verify_exact_mount", snapshot)
        self.assertIn('--spindown "$EXFAT_SNAPSHOT_DISK_LABEL"', snapshot)
        self.assertIn('"$EXFAT_SNAPSHOT_STAMP"', watchdog)

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

    def test_unattended_backup_obeys_requested_hdd_policy(self):
        backup = (BACKUP_DIR / "pi_backup.sh").read_text(encoding="utf-8")

        policy_gate = 'backup_policy_allows_hdds'
        backup_disk = 'ensure_mounted "$BACKUP_DISK_LABEL" "$BACKUP_MNT"'
        self.assertIn('"$policyctl" read', backup)
        self.assertIn("requested policy disables HDDs", backup)
        self.assertLess(backup.index(policy_gate), backup.index(backup_disk))
        self.assertIn('"$mount_disks" "$label" || return 1', backup)
        self.assertNotIn('umount -l "$mnt"', backup)
        self.assertNotIn('mount "$dev" "$mnt"', backup)

    def test_borg_excludes_pi_build_contents(self):
        config = (BACKUP_DIR / "backup_conf.sh").read_text(encoding="utf-8")
        backup = (BACKUP_DIR / "pi_backup.sh").read_text(encoding="utf-8")

        self.assertIn("BORG_EXCLUDES=(", config)
        self.assertIn("'/home/pi/build/*'", config)
        self.assertIn('for exclude in "${BORG_EXCLUDES[@]}"', backup)
        self.assertIn('borg_exclude_args+=(--exclude "$exclude")', backup)
        self.assertIn('"${borg_exclude_args[@]}"', backup)
        self.assertNotIn("--exclude '/home/pi/build/*'", backup)


if __name__ == "__main__":
    unittest.main()
