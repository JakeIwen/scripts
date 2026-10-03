from pathlib import Path
import unittest

from pi.tests.unit_contract import command_arguments, parse_directives


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
        policy_directives = parse_directives(policy_timer)
        watchdog_directives = parse_directives(watchdog_timer)

        self.assertNotIn('su pi -c "$scripts/internet_switches.sh"', crontab)
        self.assertEqual(policy_directives["OnCalendar"], ["*:0/15"])
        self.assertEqual(policy_directives["Unit"], ["vanpi-policy.service"])
        self.assertEqual(watchdog_directives["OnUnitActiveSec"], ["1min"])
        self.assertEqual(
            watchdog_directives["Unit"], ["vanpi-policy-watchdog.service"]
        )
        for unit in policy_directives["Unit"] + watchdog_directives["Unit"]:
            service = REPOSITORY_ROOT / "pi" / "services" / unit
            self.assertTrue(service.is_file(), str(service))

    def test_disk_health_watchdog_is_bounded_and_periodic(self):
        service = (
            REPOSITORY_ROOT
            / "pi"
            / "services"
            / "vanpi-disk-health-watchdog.service"
        ).read_text(encoding="utf-8")
        timer = (
            REPOSITORY_ROOT
            / "pi"
            / "services"
            / "vanpi-disk-health-watchdog.timer"
        ).read_text(encoding="utf-8")
        service_directives = parse_directives(service)
        timer_directives = parse_directives(timer)

        command = command_arguments(service_directives["ExecStart"][0])
        self.assertEqual(command, ["/home/pi/scripts/disk_health_watchdog.sh"])
        script = REPOSITORY_ROOT / "pi" / Path(*command[0].removeprefix("/home/pi/").split("/"))
        self.assertTrue(script.is_file(), str(script))
        self.assertEqual(service_directives["TimeoutStartSec"], ["35min"])
        self.assertEqual(timer_directives["OnUnitInactiveSec"], ["1min"])
        self.assertEqual(
            timer_directives["Unit"], ["vanpi-disk-health-watchdog.service"]
        )
        timer_service = REPOSITORY_ROOT / "pi" / "services" / timer_directives["Unit"][0]
        self.assertTrue(timer_service.is_file(), str(timer_service))

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
