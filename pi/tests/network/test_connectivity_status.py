import subprocess
import unittest
from types import SimpleNamespace

from pi.scripts import connectivity_status as connectivity


MWAN_SAMPLE = """Interface status:
 interface wan is offline 00h:00m:00s, uptime 94h:02m:31s and tracking is active
 interface clientwan is online 00h:35m:11s, uptime 21h:16m:11s and tracking is active
 interface lifiwan is offline and tracking is paused
__VAN_DASH_DEFAULT_POLICY__=balanced
Current ipv4 policies:
balanced:
 clientwan (100%)
wan_only:
 wan (100%)

Current ipv6 policies:
balanced:
 unreachable
__VAN_DASH_ROUTER_TIME__=1700000000
__VAN_DASH_HTTPS__=clientwan|1699999990|online|204|0|204|0
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
ccq=881
target_ssid=real-network
"""

def _iw_info(ifname, ssid, mode="AP", txpower="28.00"):
    ssid_line = f"\tssid {ssid}\n" if ssid else ""
    return (
        f"Interface {ifname}\n\tifindex 13\n{ssid_line}\ttype {mode}\n"
        f"\tchannel 1 (2412 MHz), width: 40 MHz\n\ttxpower {txpower} dBm\n"
        "\tmulticast TXQ:\n\t\tqsz-byt\tqsz-pkt\n\t\t0\t0\n"
    )


def _clients(count):
    entries = ",".join(
        '"aa:bb:cc:00:00:%02x":{"auth":true,"assoc":true,"authorized":true}' % n
        for n in range(count)
    )
    return '{"freq":2412,"clients":{%s}}\n' % entries


def _ap(ifname, iw, clients="", iw_rc=0):
    rc = f"__VAN_DASH_IW_RC__={iw_rc}\n" if iw_rc is not None else ""
    return f"__VAN_DASH_AP__={ifname}\n{iw}{rc}OBJECT hostapd.{ifname}\n{clients}"


def _radios(*blocks):
    return "__VAN_DASH_RADIOS_BEGIN__\n" + "".join(blocks) + "__VAN_DASH_RADIOS_END__\n"


NORMAL_RADIOS = _radios(
    "__VAN_DASH_RADIO__=radio0|2g|true\n",
    _ap("wl0-ap0", _iw_info("wl0-ap0", "van"), _clients(3)),
    "__VAN_DASH_RADIO__=radio1|5g|true\n",
    _ap("wl1-ap0", _iw_info("wl1-ap0", "van", txpower="27.00"), _clients(1)),
)

WAN_TRAFFIC_SAMPLE = """__VAN_DASH_WAN_STATUS__=wan={"up":true,"l3_device":"wan","device":"wan"}
__VAN_DASH_WAN_STATUS__=clientwan={"up":false,"pending":true,"device":"wl1-sta0","data":{}}
__VAN_DASH_WAN_STATUS__=lifiwan=
__VAN_DASH_UPTIME__=3833465.59 6611824.09
__VAN_DASH_BRIDGE_PORTS__=/sys/class/net/br-lan/brif/lan2 /sys/class/net/br-lan/brif/wl1-ap0 /sys/class/net/br-lan/brif/lan4
__VAN_DASH_NETDEV__=Inter-|   Receive                                                |  Transmit
__VAN_DASH_NETDEV__= face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
__VAN_DASH_NETDEV__=  eth0: 2052465901990 2231665400   11    0    0     0          0         0 2324592571462 2240242632    0    0    0     0       0          0
__VAN_DASH_NETDEV__=   wan: 793589244303 795765444    0 830449    0     0          0         0 643849733304 705908855    0   53    0     0       0          0
__VAN_DASH_NETDEV__=wl1-sta0:       0       0    0    0    0     0          0         0        0       0    0 35761    0     0       0          0
__VAN_DASH_NETDEV__=br-lan:    5000       0    0    0    0     0          0         0      900       0    0    0    0     0       0          0
__VAN_DASH_NETDEV__=  lan2:    7000       0    0    0    0     0          0         0      100       0    0    0    0     0       0          0
__VAN_DASH_NETDEV__=wl1-ap0:   1500       0    0    0    0     0          0         0      200       0    0    0    0     0       0          0
"""

MWAN_TRANSITION_SAMPLE = """Interface status:
 interface wan is online and tracking is active (online 07h:37m:37s, uptime 11h:02m:34s)
 interface clientwan is connecting and tracking is active (0)
 interface lifiwan is disconnecting and tracking is active (4)
"""


class ConnectivityParserTests(unittest.TestCase):
    def test_ping_online_https_failed_is_offline(self):
        interfaces = connectivity.parse_mwan3_interfaces(MWAN_SAMPLE)
        output = MWAN_SAMPLE.replace("online|204|0|204|0", "offline|000|28|000|28")
        connectivity.apply_https_health(interfaces, output)
        self.assertEqual(interfaces[1]["state"], "offline")
        self.assertEqual(interfaces[1]["mwan_state"], "online")

    def test_one_provider_failure_is_degraded(self):
        interfaces = connectivity.parse_mwan3_interfaces(MWAN_SAMPLE)
        output = MWAN_SAMPLE.replace("online|204|0|204|0", "degraded|204|0|503|0")
        connectivity.apply_https_health(interfaces, output)
        self.assertEqual(interfaces[1]["state"], "degraded")
        self.assertEqual(connectivity.select_mode(interfaces), "clientwan")

    def test_missing_stale_future_or_malformed_health_is_not_online(self):
        for output in (
            "", MWAN_SAMPLE.replace("1699999990", "1699999909"),
            MWAN_SAMPLE.replace("1699999990", "1700000001"),
            MWAN_SAMPLE.replace("|204|0|204|0", "|garbage"),
            MWAN_SAMPLE.replace("|online|", "|down|"),
        ):
            with self.subTest(output=output):
                interfaces = connectivity.parse_mwan3_interfaces(MWAN_SAMPLE)
                connectivity.apply_https_health(interfaces, output)
                self.assertEqual(interfaces[1]["state"], "unknown")
                self.assertEqual(interfaces[0]["state"], "offline")

    def test_selected_route_health_ignores_healthy_backup(self):
        router = {
            "default_policy": "balanced",
            "route_members": [{"name": "wan", "percent": 100}],
            "interfaces": [{"name": "wan", "state": "offline"},
                           {"name": "clientwan", "state": "online"}],
        }
        self.assertFalse(connectivity.selected_route_health(router))
        router["interfaces"][0]["state"] = "unknown"
        self.assertIsNone(connectivity.selected_route_health(router))
        router["interfaces"][0]["state"] = "degraded"
        self.assertTrue(connectivity.selected_route_health(router))
        router["route_members"] = []
        self.assertFalse(connectivity.selected_route_health(router))
        router["default_policy"] = None
        self.assertIsNone(connectivity.selected_route_health(router))

    def test_weighted_route_with_failed_member_is_not_healthy(self):
        router = {
            "default_policy": "balanced",
            "route_members": [{"name": "wan", "percent": 40},
                              {"name": "clientwan", "percent": 60}],
            "interfaces": [{"name": "wan", "state": "offline"},
                           {"name": "clientwan", "state": "online"}],
        }
        self.assertFalse(connectivity.selected_route_health(router))

    def test_mwan_parser_and_priority(self):
        interfaces = connectivity.parse_mwan3_interfaces(MWAN_SAMPLE)
        self.assertEqual([item["name"] for item in interfaces], ["wan", "clientwan", "lifiwan"])
        self.assertEqual(connectivity.select_mode(interfaces), "clientwan")
        self.assertEqual(interfaces[2]["tracking"], "paused")
        policy, members = connectivity.parse_mwan3_default_route(MWAN_SAMPLE)
        self.assertEqual(policy, "balanced")
        self.assertEqual(members, [{"name": "clientwan", "percent": 100}])

    def test_mwan_parser_preserves_transitional_interfaces(self):
        interfaces = connectivity.parse_mwan3_interfaces(MWAN_TRANSITION_SAMPLE)
        self.assertEqual(
            [(item["name"], item["state"]) for item in interfaces],
            [
                ("wan", "online"),
                ("clientwan", "connecting"),
                ("lifiwan", "disconnecting"),
            ],
        )
        self.assertEqual(connectivity.select_mode(interfaces), "wan")

    def test_wan_traffic_reads_counters_of_up_uplinks_only(self):
        self.assertEqual(
            connectivity.parse_wan_traffic(WAN_TRAFFIC_SAMPLE),
            {
                "uptime_seconds": 3833465.59,
                "uplinks": [
                    {
                        "name": "wan",
                        "device": "wan",
                        "rx_bytes": 793589244303,
                        "tx_bytes": 643849733304,
                    }
                ],
                # lan4 is a bridge port with no counters; it is skipped.
                "lan": {"ports_rx_bytes": 8500, "bridge_rx_bytes": 5000},
            },
        )
        without_bridge = WAN_TRAFFIC_SAMPLE.replace("__VAN_DASH_BRIDGE_PORTS__=", "")
        self.assertIsNone(connectivity.parse_wan_traffic(without_bridge)["lan"])

    def test_wan_traffic_counts_a_shared_device_once(self):
        output = WAN_TRAFFIC_SAMPLE.replace(
            '=clientwan={"up":false,"pending":true,"device":"wl1-sta0","data":{}}',
            '=clientwan={"up":true,"device":"wan"}',
        )
        uplinks = connectivity.parse_wan_traffic(output)["uplinks"]
        self.assertEqual([item["name"] for item in uplinks], ["wan"])

    def test_wan_traffic_without_router_clock_or_counters_is_unknown(self):
        without_uptime = WAN_TRAFFIC_SAMPLE.replace("__VAN_DASH_UPTIME__=", "")
        without_counters = "\n".join(
            line for line in WAN_TRAFFIC_SAMPLE.splitlines() if "NETDEV" not in line
        )
        for output in ("", MWAN_SAMPLE, without_uptime, without_counters):
            with self.subTest(output=output[:40]):
                self.assertIsNone(connectivity.parse_wan_traffic(output))

    def test_wan_traffic_skips_malformed_or_unknown_devices(self):
        for status in ("{not json", '["wan"]', '{"up":true,"l3_device":["wan"]}',
                       '{"up":true,"l3_device":"missing0"}', '{"up":"true","device":"wan"}'):
            with self.subTest(status=status):
                output = WAN_TRAFFIC_SAMPLE.replace(
                    '{"up":true,"l3_device":"wan","device":"wan"}', status
                )
                self.assertEqual(connectivity.parse_wan_traffic(output)["uplinks"], [])

    def test_configured_ssid_does_not_imply_association(self):
        status = connectivity.parse_ubnt_wireless(DISCONNECTED_UBNT)
        self.assertEqual(status["ssid"], "denlink")
        self.assertFalse(status["connected"])
        self.assertEqual(status["ccq_percent"], 0)

    def test_weighted_default_route_preserves_each_policy_share(self):
        weighted = MWAN_SAMPLE.replace(
            " clientwan (100%)", " clientwan (60%)\n wan (40%)", 1
        )
        policy, members = connectivity.parse_mwan3_default_route(weighted)
        self.assertEqual(policy, "balanced")
        self.assertEqual(
            members,
            [
                {"name": "clientwan", "percent": 60},
                {"name": "wan", "percent": 40},
            ],
        )

    def test_connected_radio_metrics(self):
        status = connectivity.parse_ubnt_wireless(CONNECTED_UBNT)
        self.assertTrue(status["connected"])
        self.assertEqual(status["ssid"], "real-network")
        self.assertEqual(status["signal_dbm"], -61)
        self.assertEqual(status["noise_dbm"], -96)
        self.assertEqual(status["quality_percent"], 74)
        self.assertEqual(status["ccq_percent"], 88.1)
        self.assertEqual(status["bitrate"], "65 Mb/s")

    def test_collector_reuses_mwan_state_and_passively_reads_ubnt(self):
        calls = []

        def command(args, timeout):
            calls.append(tuple(args))
            if args[-1] == connectivity.MWAN_STATUS_COMMAND:
                return SimpleNamespace(
                    returncode=0, stdout=MWAN_SAMPLE + WAN_TRAFFIC_SAMPLE, stderr=""
                )
            if args[0] == connectivity.PING:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout=DISCONNECTED_UBNT, stderr="")

        status = connectivity.collect_status(command=command, wall_clock=lambda: 1_700_000_000)
        self.assertTrue(status["internet"]["online"])
        self.assertEqual(status["router"]["mode"], "clientwan")
        self.assertEqual(status["router"]["default_policy"], "balanced")
        self.assertEqual(
            status["router"]["route_members"],
            [{"name": "clientwan", "percent": 100}],
        )
        self.assertEqual(
            [item["name"] for item in status["router"]["wan_traffic"]["uplinks"]], ["wan"]
        )
        self.assertTrue(status["ubnt"]["reachable"])
        self.assertFalse(status["ubnt"]["connected"])
        self.assertEqual(len(calls), 3)
        self.assertFalse(any("scan" in " ".join(call).lower() for call in calls))

    def test_radios_report_each_band_with_clients_and_power(self):
        radios = connectivity.parse_router_radios(MWAN_SAMPLE + NORMAL_RADIOS)
        self.assertEqual(
            radios,
            [
                {"radio": "radio0", "band": "2.4 GHz", "up": True, "broadcasting": True,
                 "txpower_dbm": 28.0, "clients": 3, "interfaces": ["wl0-ap0"]},
                {"radio": "radio1", "band": "5 GHz", "up": True, "broadcasting": True,
                 "txpower_dbm": 27.0, "clients": 1, "interfaces": ["wl1-ap0"]},
            ],
        )

    def test_beacon_stuck_ap_is_not_broadcasting(self):
        output = _radios(
            "__VAN_DASH_RADIO__=radio0|2g|true\n",
            _ap("wl0-ap0", _iw_info("wl0-ap0", None), _clients(0)),
        )
        (radio,) = connectivity.parse_router_radios(output)
        self.assertTrue(radio["up"])
        self.assertFalse(radio["broadcasting"])
        self.assertEqual(radio["txpower_dbm"], 28.0)

    def test_radio_that_is_down_has_no_interfaces(self):
        output = _radios("__VAN_DASH_RADIO__=radio1|5g|false\n")
        (radio,) = connectivity.parse_router_radios(output)
        self.assertEqual(
            (radio["up"], radio["broadcasting"], radio["txpower_dbm"], radio["clients"], radio["interfaces"]),
            (False, False, None, 0, []),
        )

    def test_station_interface_is_not_counted_as_an_ap(self):
        # The router's script only lists mode=ap interfaces; a station that
        # slipped in would show up as a second block, so check the real shape.
        output = _radios(
            "__VAN_DASH_RADIO__=radio1|5g|true\n",
            _ap("wl1-ap0", _iw_info("wl1-ap0", "van"), _clients(2)),
        )
        (radio,) = connectivity.parse_router_radios(output)
        self.assertEqual(radio["interfaces"], ["wl1-ap0"])
        self.assertEqual(radio["clients"], 2)
        self.assertIn("config.mode='ap'", connectivity.ROUTER_RADIO_STATUS_SCRIPT)

    def test_zero_dbm_txpower_is_a_number(self):
        output = _radios(
            "__VAN_DASH_RADIO__=radio0|2g|true\n",
            _ap("wl0-ap0", _iw_info("wl0-ap0", "van", txpower="0.00"), _clients(0)),
        )
        self.assertEqual(connectivity.parse_router_radios(output)[0]["txpower_dbm"], 0.0)

    def test_unreadable_hostapd_makes_clients_unknown(self):
        output = _radios(
            "__VAN_DASH_RADIO__=radio0|2g|true\n",
            _ap("wl0-ap0", _iw_info("wl0-ap0", "van"), ""),
        )
        self.assertIsNone(connectivity.parse_router_radios(output)[0]["clients"])

    def test_missing_or_garbled_radio_section_is_unknown(self):
        garbled = (
            NORMAL_RADIOS.replace("radio0|2g|true", "radio0|2g|"),
            NORMAL_RADIOS.replace("__VAN_DASH_RADIOS_END__\n", ""),
            NORMAL_RADIOS.replace("radio0|2g", "radio0|9g"),
            _radios("junk\n"),
            "",
        )
        for output in garbled:
            with self.subTest(output=output[:60]):
                self.assertIsNone(connectivity.parse_router_radios(output))

    def test_collector_keeps_mwan_state_when_radio_section_is_missing(self):
        def command(args, timeout):
            return SimpleNamespace(returncode=0, stdout=MWAN_SAMPLE, stderr="")

        router, online = connectivity.collect_router(command)
        self.assertIsNone(router["radios"])
        self.assertEqual(router["mode"], "clientwan")
        self.assertTrue(online)

    def test_collector_reports_radios_from_the_same_ssh_call(self):
        calls = []

        def command(args, timeout):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=MWAN_SAMPLE + NORMAL_RADIOS, stderr="")

        router, online = connectivity.collect_router(command)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r["band"] for r in router["radios"]], ["2.4 GHz", "5 GHz"])
        self.assertTrue(online)

    def test_failed_or_skipped_iw_is_unknown_not_stuck(self):
        cases = {
            "iw failed": _ap("wl0-ap0", "", _clients(1), iw_rc=1),
            "iw timed out": _ap("wl0-ap0", "", _clients(1), iw_rc=124),
            "iw skipped": _ap("wl0-ap0", "", _clients(1), iw_rc=None),
        }
        for name, block in cases.items():
            with self.subTest(name):
                output = _radios("__VAN_DASH_RADIO__=radio0|2g|true\n", block)
                (radio,) = connectivity.parse_router_radios(output)
                self.assertIsNone(radio["broadcasting"])
                self.assertIsNone(radio["txpower_dbm"])
                self.assertEqual(radio["clients"], 1)

    def test_unknown_ap_does_not_hide_a_broadcasting_one(self):
        output = _radios(
            "__VAN_DASH_RADIO__=radio0|2g|true\n",
            _ap("wl0-ap0", "", _clients(0), iw_rc=1),
            _ap("wl0-ap1", _iw_info("wl0-ap1", "guest"), _clients(0)),
        )
        (radio,) = connectivity.parse_router_radios(output)
        self.assertTrue(radio["broadcasting"])
        self.assertEqual(radio["interfaces"], ["wl0-ap0", "wl0-ap1"])

    def test_hostapd_payload_without_a_clients_object_is_unknown(self):
        for payload in ("{}\n", '{"freq":2412}\n', '{"clients":[]}\n', '{"clients":null}\n', "[]\n"):
            with self.subTest(payload=payload):
                output = _radios(
                    "__VAN_DASH_RADIO__=radio0|2g|true\n",
                    _ap("wl0-ap0", _iw_info("wl0-ap0", "van"), payload),
                )
                self.assertIsNone(connectivity.parse_router_radios(output)[0]["clients"])

    def test_empty_clients_object_is_zero_clients(self):
        output = _radios(
            "__VAN_DASH_RADIO__=radio0|2g|true\n",
            _ap("wl0-ap0", _iw_info("wl0-ap0", "van"), '{"freq":2412,"clients":{}}\n'),
        )
        self.assertEqual(connectivity.parse_router_radios(output)[0]["clients"], 0)

    def test_radio_section_stall_keeps_completed_mwan_evidence(self):
        partial = MWAN_SAMPLE + f"{connectivity.MWAN_END_MARKER}\n__VAN_DASH_RADIOS_BEGIN__\n"
        for stdout in (partial, partial.encode()):
            with self.subTest(type(stdout).__name__):
                def command(args, timeout, stdout=stdout):
                    raise subprocess.TimeoutExpired(args, timeout, output=stdout)

                router, online = connectivity.collect_router(command)
                self.assertTrue(router["reachable"])
                self.assertIsNone(router["radios"])
                self.assertEqual(router["mode"], "clientwan")
                self.assertTrue(online)

    def test_timeout_before_mwan_finished_is_unreachable(self):
        def command(args, timeout):
            raise subprocess.TimeoutExpired(args, timeout, output=MWAN_SAMPLE.encode())

        router, online = connectivity.collect_router(command)
        self.assertFalse(router["reachable"])
        self.assertIsNone(online)

    def test_remote_radio_calls_are_bounded(self):
        script = connectivity.ROUTER_RADIO_STATUS_SCRIPT
        self.assertEqual(script.count("/bin/ubus"), script.count("/bin/ubus -t "))
        self.assertIn("/usr/bin/timeout", script)
        worst = (
            connectivity.RADIO_SECTION_BUDGET_SECS + 2 * connectivity.RADIO_CALL_SECS
        )
        self.assertLessEqual(worst, 5)

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
