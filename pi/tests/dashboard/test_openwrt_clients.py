import json
import pathlib
import subprocess
import unittest
from types import SimpleNamespace

from pi.apps.van_dashboard import van_dashboard as dashboard
from pi.scripts import connectivity_status


def client_payload():
    return {
        "version": 1,
        "checked_at": 1_700_000_000,
        "client_count": 1,
        "wifi_count": 1,
        "lan_count": 0,
        "clients": [
            {
                "name": "m4mac",
                "hostname_known": True,
                "ip": "192.168.6.116",
                "mac": "5a:d2:10:cf:72:43",
                "connection": "wifi",
                "interface": "wl0-ap0",
                "radio": "radio0",
                "band": "2.4 GHz",
                "neighbor_state": "REACHABLE",
                "signal_dbm": -29,
                "rx_rate_bps": 14_440_000,
                "tx_rate_bps": 13_000_000,
                "rx_bytes": 726_265_377,
                "tx_bytes": 1_717_619_161,
                "lease_expires_at": 1_700_010_000,
            }
        ],
    }


class OpenWrtClientCollectorTests(unittest.TestCase):
    SAMPLE = """\
__VAN_DASH_LEASES__
1700010000 5a:d2:10:cf:72:43 192.168.6.116 m4mac 01:5a:d2:10:cf:72:43
1700010000 aa:bb:cc:dd:ee:ff 192.168.6.117 ipad 01:aa:bb:cc:dd:ee:ff
__VAN_DASH_NEIGHBORS__
192.168.6.116 lladdr 5a:d2:10:cf:72:43 STALE
192.168.6.103 lladdr dc:a6:32:94:7d:06 REACHABLE
192.168.6.200 lladdr 78:28:ca:20:f2:1a STALE
__VAN_DASH_STATIC_HOSTS__
dhcp.@host[0].name='vanpi'
dhcp.@host[0].ip='192.168.6.103'
dhcp.@host[0].mac='DC:A6:32:94:7D:06'
__VAN_DASH_HOSTAPD__
OBJECT hostapd.wl0-ap0
{"clients":{"5a:d2:10:cf:72:43":{"assoc":true,"authorized":true,"signal":-29,"rate":{"rx":14440000,"tx":13000000},"bytes":{"rx":726265377,"tx":1717619161}},"78:28:ca:20:f2:1a":{"assoc":false,"authorized":false,"signal":-50}}}
OBJECT hostapd.wl1-ap0
{"clients":{"aa:bb:cc:dd:ee:ff":{"assoc":true,"authorized":true,"signal":-41,"rate":{"rx":240200000,"tx":216200000},"bytes":{"rx":1000,"tx":2000}}}}
__VAN_DASH_RADIOS__
wl0-ap0|radio0|2g
wl1-ap0|radio1|5g
"""

    def test_parser_joins_wireless_neighbors_leases_and_static_names(self):
        status = connectivity_status.parse_router_clients(
            self.SAMPLE,
            checked_at=1_700_000_000,
        )

        self.assertEqual(status["client_count"], 3)
        self.assertEqual(status["wifi_count"], 2)
        self.assertEqual(status["lan_count"], 1)
        by_name = {client["name"]: client for client in status["clients"]}
        self.assertEqual(by_name["m4mac"]["connection"], "wifi")
        self.assertEqual(by_name["m4mac"]["radio"], "radio0")
        self.assertEqual(by_name["m4mac"]["band"], "2.4 GHz")
        self.assertEqual(by_name["m4mac"]["signal_dbm"], -29)
        self.assertEqual(by_name["m4mac"]["ip"], "192.168.6.116")
        self.assertEqual(by_name["ipad"]["connection"], "wifi")
        self.assertEqual(by_name["ipad"]["radio"], "radio1")
        self.assertEqual(by_name["ipad"]["band"], "5 GHz")
        self.assertEqual(by_name["vanpi"]["connection"], "lan")
        self.assertIsNone(by_name["vanpi"]["radio"])
        self.assertIsNone(by_name["vanpi"]["band"])
        self.assertEqual(by_name["vanpi"]["ip"], "192.168.6.103")
        self.assertNotIn("78:28:ca:20:f2:1a", {item["mac"] for item in status["clients"]})

    def test_collection_uses_one_fixed_bounded_router_command(self):
        calls = []

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(returncode=0, stdout=self.SAMPLE, stderr="")

        status = connectivity_status.collect_clients(
            command=command,
            wall_clock=lambda: 1_700_000_000,
        )

        self.assertEqual(status["client_count"], 3)
        self.assertEqual(
            calls,
            [
                (
                    connectivity_status._ssh_args(connectivity_status.ROUTER_TARGET)
                    + [connectivity_status.ROUTER_CLIENTS_COMMAND],
                    12,
                )
            ],
        )

    def test_collection_propagates_router_failure_without_fabricating_devices(self):
        def command(_args, timeout):
            self.assertEqual(timeout, 12)
            return SimpleNamespace(returncode=255, stdout="", stderr="router unavailable")

        with self.assertRaisesRegex(RuntimeError, "router unavailable"):
            connectivity_status.collect_clients(command=command)


class OpenWrtClientDashboardTests(unittest.TestCase):
    def test_controller_uses_fixed_clients_mode_and_validates_payload(self):
        calls = []
        payload = client_payload()

        def command(args, timeout):
            calls.append((list(args), timeout))
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

        controller = dashboard.OpenWrtClientsController(
            collector="/test/connectivity",
            command=command,
            timeout=7,
        )
        self.assertEqual(controller.status(), payload)
        self.assertEqual(calls, [(["/test/connectivity", "--clients"], 7)])

        invalid = {**payload, "client_count": 2}
        with self.assertRaisesRegex(
            dashboard.OpenWrtClientsError,
            "inconsistent counts",
        ):
            controller.parse_status(json.dumps(invalid))

        invalid_radio = json.loads(json.dumps(payload))
        invalid_radio["clients"][0]["radio"] = "radio0"
        invalid_radio["clients"][0]["band"] = None
        with self.assertRaisesRegex(
            dashboard.OpenWrtClientsError,
            "invalid device data",
        ):
            controller.parse_status(json.dumps(invalid_radio))

    def test_controller_reports_timeout(self):
        def command(args, timeout):
            raise subprocess.TimeoutExpired(args, timeout)

        controller = dashboard.OpenWrtClientsController(
            collector="/test/connectivity",
            command=command,
            timeout=3,
        )
        with self.assertRaisesRegex(
            dashboard.OpenWrtClientsError,
            "timed out after 3 seconds",
        ):
            controller.status()

    def test_route_returns_uncached_status_and_bounded_errors(self):
        payload = client_payload()

        class FakeClients:
            def __init__(self):
                self.error = False

            def status(self):
                if self.error:
                    raise dashboard.OpenWrtClientsError("router unavailable")
                return payload

        fake = FakeClients()
        original = dashboard.openwrt_clients
        dashboard.openwrt_clients = fake
        try:
            client = dashboard.app.test_client()
            response = client.get("/api/openwrt/clients")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertEqual(response.json["openwrt"], payload)

            fake.error = True
            failed = client.get("/api/openwrt/clients")
            self.assertEqual(failed.status_code, 502)
            self.assertIn("router unavailable", failed.json["message"])
        finally:
            dashboard.openwrt_clients = original

    def test_tile_opens_accessible_client_sheet(self):
        root = pathlib.Path(__file__).resolve().parents[3]
        application = root / "pi" / "apps" / "van_dashboard"
        template = (application / "templates" / "van_dashboard.html").read_text()
        javascript = (application / "static" / "van_dashboard.js").read_text()
        stylesheet = (application / "static" / "van_dashboard.css").read_text()

        self.assertIn('id="openwrt-open"', template)
        self.assertIn('aria-controls="openwrt-panel"', template)
        self.assertIn('id="openwrt-client-list"', template)
        self.assertIn("function openOpenwrt()", javascript)
        self.assertIn("json('/api/openwrt/clients')", javascript)
        self.assertIn("[client.band, client.radio]", javascript)
        self.assertIn(".openwrt-client-list", stylesheet)
        self.assertIn("grid-template-columns: repeat(2, minmax(0, 1fr))", stylesheet)


if __name__ == "__main__":
    unittest.main()
