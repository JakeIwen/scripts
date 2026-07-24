from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class PolicyDeploymentTests(unittest.TestCase):
    def test_slow_timer_and_independent_watchdog_replace_minutely_cron(self):
        crontab = (REPOSITORY_ROOT / "pi" / "crontab").read_text(encoding="utf-8")
        policy_timer = (
            REPOSITORY_ROOT / "pi" / "services" / "vanpi-policy.timer"
        ).read_text(encoding="utf-8")
        watchdog_timer = (
            REPOSITORY_ROOT
            / "pi"
            / "services"
            / "vanpi-policy-watchdog.timer"
        ).read_text(encoding="utf-8")

        self.assertNotIn('su pi -c "$scripts/internet_switches.sh"', crontab)
        self.assertIn("OnCalendar=*:0/15", policy_timer)
        self.assertIn("Unit=vanpi-policy.service", policy_timer)
        self.assertIn("OnUnitActiveSec=1min", watchdog_timer)
        self.assertIn("Unit=vanpi-policy-watchdog.service", watchdog_timer)

    def test_failed_legacy_cron_jobs_are_absent(self):
        crontab = (REPOSITORY_ROOT / "pi" / "crontab").read_text(encoding="utf-8")

        self.assertNotIn("tfiles_bkup", crontab)
        self.assertNotIn("copy_tfiles", crontab)
        self.assertNotIn("'/var/log/cron/*.log'", crontab)

    def test_deployer_installs_timers_and_retires_old_scripts(self):
        updater = (
            REPOSITORY_ROOT / "pi" / "scripts" / "update_services.sh"
        ).read_text(encoding="utf-8")

        self.assertIn('"$staged_services"/*.timer', updater)
        self.assertIn('"$live_scripts/rsync_to_clone.sh"', updater)
        self.assertIn('"$live_scripts/setup_router_policy_trigger.sh"', updater)

    def test_router_trigger_sources_and_backup_requirements_are_retired(self):
        self.assertFalse(
            (REPOSITORY_ROOT / "vanrouter" / "etc" / "mwan3.user").exists()
        )
        self.assertFalse(
            (
                REPOSITORY_ROOT
                / "vanrouter"
                / "usr"
                / "libexec"
                / "vanpi-policy-trigger"
            ).exists()
        )
        for path in (
            REPOSITORY_ROOT / "vanrouter" / "etc" / "sysupgrade.conf",
            REPOSITORY_ROOT
            / "vanrouter"
            / "usr"
            / "libexec"
            / "openwrt-backup-export",
            REPOSITORY_ROOT / "pi" / "scripts" / "backup" / "openwrt_backup.sh",
        ):
            self.assertNotIn(
                "vanpi-policy-trigger",
                path.read_text(encoding="utf-8"),
                str(path),
            )


if __name__ == "__main__":
    unittest.main()
