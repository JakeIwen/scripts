from pathlib import Path
import unittest

from pi.tests.unit_contract import command_arguments, parse_directives


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class PolicyDeploymentTests(unittest.TestCase):
    def test_slow_timer_and_independent_watchdog_replace_minutely_cron(self):
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
        script = REPOSITORY_ROOT / "pi" / Path(*command[0].removeprefix("/home/pi/").split("/"))
        self.assertTrue(script.is_file(), str(script))
        timer_service = REPOSITORY_ROOT / "pi" / "services" / timer_directives["Unit"][0]
        self.assertTrue(timer_service.is_file(), str(timer_service))

if __name__ == "__main__":
    unittest.main()
