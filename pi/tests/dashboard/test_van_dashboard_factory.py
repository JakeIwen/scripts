import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from pi.apps.van_dashboard import runtime
from pi.apps.van_dashboard import van_dashboard as dashboard_module


class DashboardFactoryTests(unittest.TestCase):
    def test_factory_does_not_start_runtime_workers(self):
        with (
            mock.patch.object(runtime.cop_alert, "start") as cop_start,
            mock.patch.object(runtime.connectivity, "start") as connectivity_start,
            mock.patch.object(runtime.starlink, "start") as starlink_start,
        ):
            dashboard_module.create_app()

        cop_start.assert_not_called()
        connectivity_start.assert_not_called()
        starlink_start.assert_not_called()

    def test_cop_cli_releases_fake_gpio_without_optional_dependencies(self):
        fake_gpiod = textwrap.dedent(
            """\
            import json
            import os

            LINE_REQ_DIR_OUT = "LINE_REQ_DIR_OUT"
            LINE_REQ_FLAG_BIAS_PULL_DOWN = "LINE_REQ_FLAG_BIAS_PULL_DOWN"

            def _record(event, **details):
                with open(os.environ["FAKE_GPIOD_LOG"], "a", encoding="utf-8") as handle:
                    json.dump({"event": event, **details}, handle)
                    handle.write("\\n")


            class Line:
                def name(self):
                    return "GPIO17"

                def request(self, consumer, type, flags, default_val):
                    _record(
                        "request",
                        consumer=consumer,
                        type=type,
                        flags=flags,
                        default_val=default_val,
                        exclusive=type == LINE_REQ_DIR_OUT,
                    )

                def get_value(self):
                    _record("get_value")
                    return 0

                def set_value(self, value):
                    _record("set_value", value=value)

                def release(self):
                    _record("release")


            class Chip:
                def __init__(self, path):
                    _record("chip", path=path)

                def label(self):
                    return "pinctrl-bcm2711"

                def get_line(self, offset):
                    _record("get_line", offset=offset)
                    return Line()

                def close(self):
                    _record("close")
            """
        )
        repo_root = Path(__file__).resolve().parents[3]

        with tempfile.TemporaryDirectory() as tempdir:
            temporary = Path(tempdir)
            fake_dir = temporary / "fake-gpiod"
            fake_dir.mkdir()
            (fake_dir / "gpiod.py").write_text(fake_gpiod, encoding="utf-8")
            log_path = temporary / "gpiod-log.jsonl"
            environment = dict(os.environ)
            environment.update(
                {
                    "FAKE_GPIOD_LOG": str(log_path),
                    "PYTHONPATH": os.pathsep.join((str(repo_root), str(fake_dir))),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "VAN_DASHBOARD_RUNTIME_DIR": str(temporary / "runtime"),
                    "VAN_DASHBOARD_STATE_PATH": str(temporary / "state.json"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-P",
                    "-m",
                    "pi.apps.van_dashboard.van_dashboard_cop",
                    "--relay-off",
                ],
                cwd=tempdir,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            events = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(
            [event for event in events if event["event"] in {"chip", "get_line"}],
            [{"event": "chip", "path": "gpiochip0"}, {"event": "get_line", "offset": 17}],
        )
        self.assertEqual(
            [event for event in events if event["event"] == "request"],
            [
                {
                    "event": "request",
                    "consumer": "cop-alert-light",
                    "type": "LINE_REQ_DIR_OUT",
                    "flags": "LINE_REQ_FLAG_BIAS_PULL_DOWN",
                    "default_val": 0,
                    "exclusive": True,
                }
            ],
        )
        self.assertEqual(
            [event["value"] for event in events if event["event"] == "set_value"],
            [0],
        )
        self.assertEqual(
            [event["event"] for event in events if event["event"] in {"release", "close"}],
            ["release", "close"],
        )

    def test_main_registers_signal_starts_workers_and_stops_in_finally(self):
        app = mock.Mock()
        app.run.side_effect = RuntimeError("server stopped")
        with (
            mock.patch.object(dashboard_module, "create_app", return_value=app),
            mock.patch.object(dashboard_module.signal, "signal") as signal_handler,
            mock.patch.object(runtime.cop_alert, "start") as cop_start,
            mock.patch.object(runtime.connectivity, "start") as connectivity_start,
            mock.patch.object(runtime.starlink, "start") as starlink_start,
            mock.patch.object(runtime.cop_alert, "stop") as cop_stop,
        ):
            with self.assertRaises(RuntimeError):
                dashboard_module.main()

        app.run.assert_called_once_with(
            host="0.0.0.0",
            port=dashboard_module.PORT,
            threaded=True,
        )
        cop_start.assert_called_once_with()
        connectivity_start.assert_called_once_with()
        starlink_start.assert_called_once_with()
        cop_stop.assert_called_once_with()
        signal_handler.assert_called_once()
        signum, handler = signal_handler.call_args.args
        self.assertEqual(signum, signal.SIGTERM)
        with self.assertRaises(SystemExit) as caught:
            handler(None, None)
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
