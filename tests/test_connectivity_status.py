import unittest
from types import SimpleNamespace

from pi.scripts import connectivity_status as connectivity


MWAN_SAMPLE = """Interface status:
 interface wan is offline 00h:00m:00s, uptime 94h:02m:31s and tracking is active
 interface clientwan is online 00h:35m:11s, uptime 21h:16m:11s and tracking is active
 interface lifiwan is offline and tracking is paused
"""

DISCONNECTED_UBNT = """ath0 IEEE 802.11ng ESSID:"STARLINK"
 Mode:Managed Frequency:2.424 GHz Access Point: Not-Associated
 Bit Rate:0 kb/s
 Link Quality=0/94 Signal level=-96 dBm Noise level=-96 dBm
ccq=0
target_ssid=denlink
"""

CONNECTED_UBNT = """ath0 IEEE 802.11ng ESSID:"profile-label"
 Mode:Managed Frequency:2.437 GHz Access Point: 00:11:22:33:44:55
 Bit Rate=65 Mb/s
 Link Quality=70/94 Signal level=-61 dBm Noise level=-96 dBm
ccq=88
target_ssid=real-network
"""


class ConnectivityParserTests(unittest.TestCase):
    def test_mwan_parser_and_priority(self):
        interfaces = connectivity.parse_mwan3_interfaces(MWAN_SAMPLE)
        self.assertEqual([item["name"] for item in interfaces], ["wan", "clientwan", "lifiwan"])
        self.assertEqual(connectivity.select_mode(interfaces), "clientwan")
        self.assertEqual(interfaces[2]["tracking"], "paused")

    def test_configured_ssid_does_not_imply_association(self):
        status = connectivity.parse_ubnt_wireless(DISCONNECTED_UBNT)
        self.assertEqual(status["ssid"], "denlink")
        self.assertFalse(status["connected"])
        self.assertEqual(status["ccq_percent"], 0)

    def test_connected_radio_metrics(self):
        status = connectivity.parse_ubnt_wireless(CONNECTED_UBNT)
        self.assertTrue(status["connected"])
        self.assertEqual(status["ssid"], "real-network")
        self.assertEqual(status["signal_dbm"], -61)
        self.assertEqual(status["noise_dbm"], -96)
        self.assertEqual(status["quality_percent"], 74)
        self.assertEqual(status["ccq_percent"], 88)
        self.assertEqual(status["bitrate"], "65 Mb/s")

    def test_collector_reuses_mwan_state_and_passively_reads_ubnt(self):
        calls = []

        def command(args, timeout):
            calls.append(tuple(args))
            if args[-1] == "/usr/sbin/mwan3 interfaces":
                return SimpleNamespace(returncode=0, stdout=MWAN_SAMPLE, stderr="")
            if args[0] == connectivity.PING:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=DISCONNECTED_UBNT, stderr="")

        status = connectivity.collect_status(command=command, wall_clock=lambda: 1_700_000_000)
        self.assertTrue(status["internet"]["online"])
        self.assertEqual(status["router"]["mode"], "clientwan")
        self.assertTrue(status["ubnt"]["reachable"])
        self.assertFalse(status["ubnt"]["connected"])
        self.assertEqual(len(calls), 3)
        self.assertFalse(any("scan" in " ".join(call).lower() for call in calls))

    def test_unreachable_ubnt_skips_radio_ssh(self):
        calls = []

        def command(args, timeout):
            calls.append(tuple(args))
            if args[0] == connectivity.PING:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=MWAN_SAMPLE, stderr="")

        status = connectivity.collect_status(command=command)
        self.assertFalse(status["ubnt"]["reachable"])
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
