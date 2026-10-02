import threading
import unittest
from unittest import mock

from pi.apps.van_dashboard.van_dashboard_network import UbntWifiController


class StarlinkConnectionTests(unittest.TestCase):
    def manager(self):
        manager = UbntWifiController()
        manager.STARLINK_BOOT_SECONDS = 0
        manager.STARLINK_DISCOVERY_SECONDS = 0
        return manager

    def wifi(self, visible=False):
        return {"version": 1, "checked_at": 123, "reachable": True,
                "state": {"selector_running": False, "associated_ssid": "Old",
                          "configured_ssid": "Old", "ccq_percent": 99},
                "profiles": [{"name": "denlink"}],
                "networks": [{"profiles": ["denlink"]}] if visible else []}

    def test_waits_for_visible_denlink_before_connecting(self):
        manager = self.manager()
        manager.STARLINK_DISCOVERY_SECONDS = 180
        event = mock.Mock()
        event.is_set.return_value = False
        event.wait.return_value = False
        result = {"wifi": self.wifi(True), "message": "connected"}
        with mock.patch.object(manager, "_tool_result", side_effect=[
            {"wifi": self.wifi()}, {"wifi": self.wifi()},
            {"wifi": self.wifi()}, {"wifi": self.wifi(True)}, result,
        ]) as tool:
            self.assertEqual(manager._connect_starlink(event), result)
        self.assertEqual([c.args[0] for c in tool.call_args_list],
                         ["status", "scan", "status", "scan", "connect"])
        self.assertEqual(tool.call_args.args[1], {"profile": "denlink"})

    def test_does_not_retry_a_failed_connection_mutation(self):
        manager = self.manager()
        with mock.patch.object(manager, "_tool_result", side_effect=[
            {"wifi": self.wifi()}, {"wifi": self.wifi(True)}, RuntimeError("connect failed"),
        ]) as tool:
            with self.assertRaisesRegex(RuntimeError, "connect failed"):
                manager._connect_starlink(threading.Event())
        self.assertEqual(tool.call_count, 3)

    def test_missing_network_times_out_without_connecting(self):
        manager = self.manager()
        with mock.patch.object(manager, "_tool_result", return_value={"wifi": self.wifi()}) as tool:
            with self.assertRaisesRegex(RuntimeError, "did not become available"):
                manager._connect_starlink(threading.Event())
        self.assertEqual([c.args[0] for c in tool.call_args_list], ["status", "scan"])

    def test_power_off_cancels_boot_wait_without_connecting(self):
        manager = self.manager()
        manager.STARLINK_BOOT_SECONDS = 60
        with mock.patch.object(manager, "_tool_result", return_value={"wifi": self.wifi()}) as tool:
            manager.start("starlink")
            boot_worker = manager.thread
            self.assertIn("boot", manager.snapshot()["operation"]["message"])
            manager.starlink_power_changed("off")
            boot_worker.join(2)
            manager.starlink_thread.join(2)
            manager.thread.join(2)
            self.assertFalse(manager.thread.is_alive())
        self.assertEqual([c.args[0] for c in tool.call_args_list], ["starlink-off"])

    def test_power_on_queues_behind_existing_operation_and_off_cancels_queue(self):
        manager = self.manager()
        manager.operation["status"] = "running"
        manager.operation["kind"] = "provision"
        with mock.patch.object(manager, "_tool_result", return_value={"wifi": self.wifi()}) as tool:
            manager.starlink_power_changed("on")
            old_queue = manager.starlink_thread
            self.assertTrue(manager.snapshot()["starlink_pending"])
            manager.starlink_power_changed("off")
            old_queue.join(2)
            self.assertFalse(old_queue.is_alive())
            self.assertEqual(manager.snapshot()["operation"]["kind"], "provision")
            tool.assert_not_called()
            with manager.lock:
                manager.operation["status"] = "complete"
            manager.starlink_thread.join(2)
            manager.thread.join(2)
        self.assertEqual([c.args[0] for c in tool.call_args_list], ["starlink-off"])
        self.assertFalse(manager.snapshot()["starlink_pending"])

    def test_power_off_when_idle_dispatches_without_boot_delay_or_status_read(self):
        manager = self.manager()
        with mock.patch.object(manager, "_tool_result", return_value={"wifi": self.wifi()}) as tool:
            manager.starlink_power_changed("off")
            manager.starlink_thread.join(2)
            manager.thread.join(2)
        self.assertEqual([c.args[0] for c in tool.call_args_list], ["starlink-off"])
        self.assertEqual(manager.snapshot()["operation"]["status"], "complete")

    def test_latest_power_on_supersedes_queued_power_off(self):
        manager = self.manager()
        manager.operation.update(status="running", kind="scan")
        with mock.patch.object(manager, "_connect_starlink", return_value={"wifi": self.wifi()}) as connect, \
                mock.patch.object(manager, "_tool_result") as tool:
            manager.starlink_power_changed("off")
            old_queue = manager.starlink_thread
            manager.starlink_power_changed("on")
            old_queue.join(2)
            with manager.lock:
                manager.operation["status"] = "complete"
            manager.starlink_thread.join(2)
            manager.thread.join(2)
        connect.assert_called_once()
        tool.assert_not_called()

    def test_power_off_waits_for_inflight_connect_before_releasing_denlink(self):
        manager = self.manager()
        entered, finish = threading.Event(), threading.Event()
        def connecting(_abort):
            entered.set()
            if not finish.wait(2):
                raise RuntimeError("test connect did not finish")
            return {"wifi": self.wifi()}
        with mock.patch.object(manager, "_connect_starlink", side_effect=connecting), \
                mock.patch.object(manager, "_tool_result", return_value={"wifi": self.wifi()}) as tool:
            manager.start("starlink")
            worker = manager.thread
            self.assertTrue(entered.wait(1))
            manager.starlink_power_changed("off")
            tool.assert_not_called()
            finish.set()
            worker.join(2)
            manager.starlink_thread.join(2)
            manager.thread.join(2)
        self.assertEqual([c.args[0] for c in tool.call_args_list], ["starlink-off"])

    def test_queued_power_on_starts_after_existing_operation(self):
        manager = self.manager()
        manager.operation["status"] = "running"
        manager.operation["kind"] = "scan"
        with mock.patch.object(manager, "_connect_starlink", return_value={"wifi": self.wifi()}) as connect:
            manager.starlink_power_changed("on")
            with manager.lock:
                manager.operation["status"] = "complete"
            manager.starlink_thread.join(2)
            manager.thread.join(2)
        connect.assert_called_once()
        self.assertEqual(manager.snapshot()["operation"]["status"], "complete")
        self.assertFalse(manager.snapshot()["starlink_pending"])

    def test_success_clears_transient_status_error(self):
        manager = self.manager()
        manager.last_error = "Connection refused"
        with mock.patch.object(manager, "_tool_result", return_value={"wifi": self.wifi()}):
            manager._run("connect", {"profile": "denlink"})
        self.assertIsNone(manager.snapshot()["last_error"])


if __name__ == "__main__":
    unittest.main()
