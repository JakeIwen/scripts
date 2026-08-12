import importlib.util
import os
import subprocess
import unittest


MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "ubnt_wifi.py",
)
SPEC = importlib.util.spec_from_file_location("ubnt_wifi", MODULE_PATH)
ubnt_wifi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ubnt_wifi)


def encoded(value):
    return value.encode().hex()


SNAPSHOT = "\n".join(
    (
        f"state|{encoded('denlink')}|{encoded('denlink')}|991|no|no|-56|-93",
        f"profile|{encoded('denlink')}|{encoded('denlink')}|wpa|10|4E:EA:85:26:34:F4|yes|21|atheros|enabled|4",
        f"network|70|{encoded('denlink')}|wpa|2462|11|4e:ea:85:26:34:f4|-30",
        f"network|90|{encoded('denlink')}|wpa|2462|11|4e:ea:85:26:34:f4|-12",
        f"network|55|{encoded('Guest WiFi')}|none|2412|1|00:11:22:33:44:55|-48",
        f"network|40|{encoded('Campus')}|enterprise|2422|3|00:11:22:33:44:77|-65",
    )
)


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class SnapshotParserTests(unittest.TestCase):
    def test_parses_known_networks_and_keeps_strongest_observation(self):
        data = ubnt_wifi.parse_snapshot(SNAPSHOT, checked_at=1234)

        self.assertEqual(data["checked_at"], 1234)
        self.assertEqual(data["state"]["ccq_percent"], 99.1)
        self.assertEqual(data["state"]["snr_db"], 37)
        self.assertFalse(data["state"]["automatic_paused"])
        self.assertEqual(len(data["networks"]), 3)
        self.assertEqual(data["networks"][0]["ssid"], "denlink")
        self.assertEqual(data["networks"][0]["quality_percent"], 90)
        self.assertTrue(data["networks"][0]["known"])
        self.assertTrue(data["networks"][0]["connected"])
        self.assertEqual(data["profiles"][0]["bssid"], "4E:EA:85:26:34:F4")
        self.assertTrue(data["profiles"][0]["has_password"])
        self.assertEqual(data["profiles"][0]["output_power_dbm"], 21)
        self.assertTrue(data["profiles"][0]["rate_auto"])
        self.assertEqual(data["networks"][1]["profiles"], [])
        self.assertFalse(data["networks"][2]["supported"])

    def test_rejects_missing_or_invalid_state(self):
        with self.assertRaises(ubnt_wifi.UbntWifiError):
            ubnt_wifi.parse_snapshot("profile|00|00|wpa")
        with self.assertRaises(ubnt_wifi.UbntWifiError):
            ubnt_wifi.parse_snapshot("state|not-hex|||no|no")


class ClientTests(unittest.TestCase):
    def test_connect_uses_fixed_remote_command_and_accepts_captive_portal_state(self):
        calls = []

        def command(args, timeout, input_text=None):
            calls.append((args, timeout, input_text))
            if args[-1].endswith("manual-connect-stdin"):
                return Result(returncode=2, stdout="associated without internet")
            return Result(stdout=SNAPSHOT)

        result = ubnt_wifi.UbntWifiClient(command=command).connect("denlink")

        self.assertEqual(result["outcome"], "associated_no_internet")
        connect_call = calls[1]
        self.assertEqual(
            connect_call[0][-1],
            "/etc/persistent/scripts/wifi_manager.sh manual-connect-stdin",
        )
        self.assertEqual(connect_call[2], "denlink\n")
        self.assertNotIn("denlink", " ".join(connect_call[0]))

    def test_provision_keeps_password_out_of_argv(self):
        calls = []

        def command(args, timeout, input_text=None):
            calls.append((args, timeout, input_text))
            if args[-1].endswith("provision-stdin"):
                return Result(stdout="provisioned")
            return Result(stdout=SNAPSHOT)

        result = ubnt_wifi.UbntWifiClient(command=command).provision(
            "New Camp", "wpa", "00:11:22:33:44:66", "secret-test-password"
        )

        self.assertEqual(result["outcome"], "connected")
        provision_call = calls[1]
        self.assertEqual(
            provision_call[0][-1],
            "/etc/persistent/scripts/wifi_manager.sh provision-stdin",
        )
        self.assertNotIn("secret-test-password", " ".join(provision_call[0]))
        self.assertNotIn("New Camp", " ".join(provision_call[0]))
        self.assertEqual(
            provision_call[2],
            "New Camp\nwpa\n00:11:22:33:44:66\nsecret-test-password\n",
        )

    def test_profile_update_is_fixed_validated_and_keeps_password_out_of_argv(self):
        calls = []

        def command(args, timeout, input_text=None):
            calls.append((args, timeout, input_text))
            if args[-1].endswith("update-profile-stdin"):
                return Result(stdout="updated")
            return Result(stdout=SNAPSHOT)

        result = ubnt_wifi.UbntWifiClient(command=command).update_profile(
            "denlink",
            "replacement-password",
            "00:11:22:33:44:66",
            18,
            "ewma_ht",
            False,
            4,
            False,
        )

        self.assertIn("next connection", result["message"])
        update_call = calls[1]
        self.assertEqual(
            update_call[0][-1],
            "/etc/persistent/scripts/wifi_manager.sh update-profile-stdin",
        )
        self.assertNotIn("replacement-password", " ".join(update_call[0]))
        self.assertEqual(
            update_call[2],
            "denlink\nchange\nreplacement-password\n00:11:22:33:44:66\n18\newma_ht\ndisabled\n4\nno\n",
        )

    def test_profile_update_can_keep_password_and_rejects_unsafe_values(self):
        calls = []

        def command(args, timeout, input_text=None):
            calls.append((args, timeout, input_text))
            return Result(stdout=SNAPSHOT)

        client = ubnt_wifi.UbntWifiClient(command=command)
        client.update_profile("denlink", "", "", 21, "atheros", True, 15, False)
        self.assertIn("\nkeep\n\n\n21\natheros\nenabled\n15\nno\n", calls[1][2])
        invalid = (
            ("denlink", "short", "", 21, "atheros", True, 15, False),
            ("denlink", "", "invalid", 21, "atheros", True, 15, False),
            ("denlink", "", "", 24, "atheros", True, 15, False),
            ("denlink", "", "", 21, "unknown", True, 15, False),
            ("denlink", "", "", 21, "atheros", True, 16, False),
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ubnt_wifi.UbntWifiError):
                client.update_profile(*values)

    def test_rejects_unsupported_or_unsafe_new_network_values(self):
        client = ubnt_wifi.UbntWifiClient(command=lambda *args, **kwargs: Result())
        invalid = (
            ("bad/name", "wpa", "00:11:22:33:44:55", "password1"),
            ("network", "wep", "00:11:22:33:44:55", "password1"),
            ("network", "wpa", "not-a-bssid", "password1"),
            ("network", "wpa", "00:11:22:33:44:55", "short"),
            ("network", "none", "00:11:22:33:44:55", "not-empty"),
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ubnt_wifi.UbntWifiError):
                client.provision(*values)

    def test_timeout_is_reported_without_command_output(self):
        def command(args, timeout, input_text=None):
            raise subprocess.TimeoutExpired(args, timeout)

        with self.assertRaisesRegex(ubnt_wifi.UbntWifiError, "timed out"):
            ubnt_wifi.UbntWifiClient(command=command).scan()


if __name__ == "__main__":
    unittest.main()
