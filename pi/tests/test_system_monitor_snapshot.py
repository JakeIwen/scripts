"""Deploy-shaped system-monitor CLI snapshot coverage."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from pi.scripts.network_recorder import parsers as package_parsers
from pi.scripts.system_monitor.common import event_fingerprint
from pi.scripts.system_monitor.store import EventStore


NOW = 1_700_100_000.0
PREVIOUS_BOOT = "fedcba9876543210fedcba9876543210"
PASSWORD_SAMPLE = "password=hunter2"
FIXTURES = Path(__file__).parent / "fixtures" / "system_monitor"


COMMANDS = {
    "report_dashboard": ["report", "--hours", "6", "--limit", "100", "--json"],
    "report_24h_limit5": ["report", "--hours", "24", "--limit", "5", "--json"],
    "report_1h": ["report", "--hours", "1", "--limit", "100", "--json"],
    "crash_history_full": ["crash-history", "--limit", "20", "--full", "--json"],
    "crash_history": ["crash-history", "--json"],
    "events_state": ["events", "--hours", "24", "--limit", "50", "--state", "--json"],
    "events_usb": [
        "events",
        "--hours",
        "24",
        "--category",
        "usb",
        "--severity",
        "warning",
        "--json",
    ],
    "report_text": ["report", "--hours", "6", "--limit", "10"],
    "crash_report": ["crash-report", "--json"],
    "crash_report_text": ["crash-report"],
    "crash_history_text": ["crash-history"],
    "events_text": ["events", "--hours", "24", "--limit", "5"],
}

# These commands intentionally run after COMMANDS.  crash-report --save mutates
# the same database, just as the deploy-time snapshot tool does.
JOURNAL_COMMANDS = {
    "journal_crash_report": ["crash-report", "--json"],
    "journal_crash_report_text": ["crash-report"],
    "journal_crash_report_save": ["crash-report", "--save", "--json"],
    "journal_crash_history_after_save": [
        "crash-history",
        "--limit",
        "20",
        "--full",
        "--json",
    ],
}


# This is a fresh-process equivalent of the snapshot tool's run_cli.  Keeping
# the fake journal inside the subprocess makes the CLI's flat import path real.
_CHILD_RUNNER = r'''\
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest import mock


NOW = 1_700_100_000.0
PREVIOUS_BOOT = "fedcba9876543210fedcba9876543210"
PASSWORD_SAMPLE = "password=hunter2"


def _journal_line(offset, message, monotonic, source="kernel", priority=4, pid=None, transport="kernel"):
    record = {
        "__REALTIME_TIMESTAMP": str(int((NOW - offset) * 1_000_000)),
        "__MONOTONIC_TIMESTAMP": str(int(monotonic * 1_000_000)),
        "_BOOT_ID": PREVIOUS_BOOT,
        "MESSAGE": message,
        "PRIORITY": str(priority),
        "SYSLOG_IDENTIFIER": source,
        "_TRANSPORT": transport,
    }
    if pid is not None:
        record["_PID"] = str(pid)
    return json.dumps(record, sort_keys=True)


JOURNAL_GENERAL = "\n".join([
    _journal_line(5000, "Started Session c1 of user pi.", 10, source="systemd", priority=6, pid=1, transport="journal"),
    _journal_line(4500, "Undervoltage detected! (0x00050005)", 500, priority=2),
    _journal_line(4490, "usb 1-1.3: device descriptor read/64, error -71", 510),
    _journal_line(4480, "usb 1-1.3: reset high-speed USB device number 4 using xhci_hcd", 520),
    _journal_line(4470, "Voltage normalised (0x00000000)", 530, priority=6),
    _journal_line(4300, "hub 1-1:1.0: over-current change on port 2", 700, priority=3),
    _journal_line(4200, "blk_update_request: I/O error, dev sda, sector 123 token=abc123", 800, priority=3),
    _journal_line(4100, "python3[999]: segfault at 0 ip 0000 sp 0000 error 4 see https://example.invalid/x?y=z", 900),
    _journal_line(4050, "Out of memory: Killed process 4242 (hass) total-vm:1kB", 950, priority=2),
    _journal_line(4010, "app crashed: core dumped password=hunter2", 990, source="myapp", priority=3, transport="journal"),
    _journal_line(4001, "INFO: task kworker blocked for more than 120 seconds.", 999, priority=3),
    "not json at all",
    json.dumps({"MESSAGE": "missing realtime"}),
])
JOURNAL_KERNEL = "\n".join([
    _journal_line(4500, "Undervoltage detected! (0x00050005)", 500, priority=2),
    _journal_line(3999, "watchdog: BUG: soft lockup - CPU#0 stuck for 22s!", 1001, priority=0),
    _journal_line(3998, "Kernel panic - not syncing: Fatal exception", 1002, priority=0),
])


def fake_subprocess_run(use_fixture):
    def run(args, *positional, **keywords):
        if isinstance(args, (list, tuple)) and args and args[0] == "/usr/bin/journalctl":
            if not use_fixture:
                return subprocess.CompletedProcess(list(args), 0, "", "")
            stdout = JOURNAL_KERNEL if "--dmesg" in args else JOURNAL_GENERAL
            return subprocess.CompletedProcess(list(args), 0, stdout + "\n", "")
        raise AssertionError(f"unexpected subprocess: {args!r}")
    return run


def isolated_read_text(path, default=None):
    assert path in ("/proc/uptime", "/proc/sys/kernel/random/boot_id")
    return default


def reject_pi_imports():
    assert "PYTHONPATH" not in os.environ
    assert importlib.util.find_spec("pi") is None
    assert not any(name == "pi" or name.startswith("pi.") for name in sys.modules)
    try:
        import pi  # noqa: F401
    except ModuleNotFoundError:
        return
    raise AssertionError("pi unexpectedly importable in staged subprocess")


def assert_staged_module(module_name, expected):
    module = sys.modules[module_name]
    assert Path(module.__file__).resolve() == expected
    return module


def assert_flat_package_shape(stage, expected_redacted):
    reject_pi_imports()
    network_recorder = assert_staged_module(
        "network_recorder", stage / "network_recorder" / "__init__.py"
    )
    assert network_recorder.__package__ == "network_recorder"
    assert [Path(path).resolve() for path in network_recorder.__path__] == [
        (stage / "network_recorder").resolve()
    ]
    parsers = assert_staged_module(
        "network_recorder.parsers", stage / "network_recorder" / "parsers.py"
    )
    assert parsers.redact(PASSWORD_SAMPLE) == expected_redacted
    assert "hunter2" not in parsers.redact(PASSWORD_SAMPLE)
    assert_staged_module("system_event_monitor", stage / "system_event_monitor.py")


def assert_cli_package_shape(stage):
    reject_pi_imports()
    assert (stage / "system_event_monitor.py").is_file()
    assert Path(sys.path[0]).resolve() == stage.resolve()
    system_monitor = assert_staged_module(
        "system_monitor", stage / "system_monitor" / "__init__.py"
    )
    assert system_monitor.__package__ == "system_monitor"
    assert [Path(path).resolve() for path in system_monitor.__path__] == [
        (stage / "system_monitor").resolve()
    ]
    for module_name in (
        "system_monitor.common",
        "system_monitor.store",
        "system_monitor.probes",
        "system_monitor.journal",
        "system_monitor.rollups",
        "system_monitor.daemon",
        "system_monitor.crash",
        "system_monitor.report",
        "system_monitor.cli",
    ):
        module = assert_staged_module(
            module_name,
            stage / "system_monitor" / (module_name.rsplit(".", 1)[1] + ".py"),
        )
        assert module.__package__ == "system_monitor"


def main():
    mode = sys.argv[1]
    script = Path(sys.argv[2]).resolve()
    stage = script.parent
    assert Path.cwd().resolve() == Path(os.environ["EXPECTED_CWD"]).resolve()
    sys.path.insert(0, str(stage))
    if mode == "parser":
        expected = os.environ["EXPECTED_REDACTED"]
        import network_recorder.parsers  # noqa: F401
        assert_flat_package_shape(stage, expected)
        return 0

    database = sys.argv[3]
    journal = sys.argv[4] == "1"
    cli_args = sys.argv[5:]
    argv = [str(script), "--database", database, *cli_args]
    import system_monitor.crash as crash
    exit_code = 0
    run_patch = mock.patch("subprocess.run", fake_subprocess_run(journal))
    with (
        mock.patch.object(sys, "argv", argv),
        mock.patch("time.time", return_value=NOW),
        run_patch,
        mock.patch.object(crash, "read_text", side_effect=isolated_read_text),
        mock.patch.object(crash, "read_pstore", return_value=[]),
        mock.patch.object(crash, "read_pstore_archive", return_value=[]),
    ):
        try:
            # The real CLI raises SystemExit before run_path can return its globals.
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exit_:
            if exit_.code is None:
                exit_code = 0
            elif isinstance(exit_.code, int):
                exit_code = exit_.code
            else:
                raise
    assert_cli_package_shape(stage)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _insert_snapshot_event(
    store, offset, kind, category="power", severity="warning", boot="boot-one"
):
    timestamp = NOW - offset
    store.insert_event(
        timestamp=timestamp,
        boot_id=boot,
        category=category,
        kind=kind,
        severity=severity,
        source="kernel",
        summary=kind.replace("_", " "),
        message=f"{kind} message https://example.invalid/secret?token=abc",
        fingerprint=event_fingerprint(timestamp, kind),
        state={"capture": "snapshot", "offset": offset},
    )


def _populate_events(store):
    _insert_snapshot_event(store, 9000, "undervoltage_started", severity="critical")
    _insert_snapshot_event(store, 8990, "undervoltage_cleared", severity="info")
    _insert_snapshot_event(store, 500, "undervoltage_started", severity="critical")
    _insert_snapshot_event(store, 496, "usb_connected", category="usb", severity="info")
    _insert_snapshot_event(store, 495, "usb_error", category="usb")
    _insert_snapshot_event(store, 494, "usb_overcurrent", category="power", severity="critical")
    _insert_snapshot_event(store, 480, "undervoltage_cleared", severity="info")
    _insert_snapshot_event(store, 300, "storage_io_error", category="storage", severity="critical")
    _insert_snapshot_event(store, 250, "usb_reset", category="usb")
    _insert_snapshot_event(store, 240, "usb_disconnected", category="usb")
    _insert_snapshot_event(store, 200, "undervoltage_started", severity="critical")
    _insert_snapshot_event(store, 194, "undervoltage_cleared", severity="info")
    _insert_snapshot_event(store, 100, "firmware_throttled_active", category="throttle", severity="critical")
    _insert_snapshot_event(store, 50, "firmware_throttled_cleared", category="throttle", severity="info")
    _insert_snapshot_event(store, 40, "kernel_oops", category="kernel", severity="critical")
    for index in range(30):
        _insert_snapshot_event(store, 3000 + index, "usb_error", category="usb")


def _snapshot_rollup(index):
    return {
        "period_start": NOW - 600 + index * 60,
        "period_end": NOW - 541 + index * 60,
        "boot_id": "boot-one",
        "sample_count": 12,
        "cpu_peak": 70 + index,
        "memory_peak": 40 + index,
        "swap_peak": index,
        "load1_peak": 1 + index,
        "temperature_peak": 80 + index,
        "root_used_peak": 50,
        "arm_mhz_min": 600 + index,
        "metrics": {
            "cpu": {
                "peak": 70 + index,
                "at": NOW - 570 + index * 60,
                "top_process": {
                    "name": "smbd" if index % 2 == 0 else "rsync",
                    "pid": 100 + index,
                    "cpu_percent": 50 + index,
                    "rss_bytes": 20_000_000,
                },
            },
            "memory": {
                "peak": 40 + index,
                "at": NOW - 565 + index * 60,
                "top_process": {"name": "hass", "pid": 200, "rss_bytes": 300_000_000 + index},
            },
            "swap": {"peak": index, "at": NOW - 560 + index * 60},
            "load1": {"peak": 1 + index, "at": NOW - 560 + index * 60},
            "temperature": {"peak": 80 + index, "at": NOW - 560 + index * 60, "average": 75},
            "root_used": {"peak": 50, "at": NOW - 560 + index * 60},
            "arm_mhz": {"peak": 600 + index, "at": NOW - 560 + index * 60},
            "network_rx": {
                "peak": 1000 * (index + 1),
                "at": NOW - 555 + index * 60,
                "top_interface": {"name": "wlan0", "physical": True, "rx_bytes_per_second": 900},
            },
            "network_tx": {"peak": 500 * (index + 1), "at": NOW - 555 + index * 60},
            "disk_read": {"peak": 2000 * (index + 1), "at": NOW - 550 + index * 60},
            "disk_write": {
                "peak": 3000 * (index + 1),
                "at": NOW - 550 + index * 60,
                "top_device": {"name": "sda", "labels": ["movingparts"]},
            },
            "disk_busy": {"peak": 10 + index, "at": NOW - 550 + index * 60},
            "thermal_sensors": [
                {
                    "zone": "thermal_zone0",
                    "type": "cpu-thermal",
                    "cpu_ids": [0, 1, 2, 3],
                    "shared": True,
                    "peak": 80 + index,
                    "average": 78,
                    "at": NOW - 540 + index * 60,
                }
            ],
        },
    }


def _populate_rollups(store):
    for index in range(5):
        store.insert_rollup(_snapshot_rollup(index))


def _current_snapshot():
    return {
        "timestamp": NOW - 3,
        "cpu_percent": 99.5,
        "memory": {"used_percent": 41, "available_bytes": 1_000_000},
        "swap": {"used_percent": 0},
        "load": {"1m": 0.2},
        "temperature_c": 55,
        "arm_mhz": 1500,
        "root_filesystem": {"used_percent": 42},
        "network_io": {
            "rx_bytes_per_second": 999_999,
            "tx_bytes_per_second": 512,
            "interfaces": [
                {"name": "eth0", "physical": True, "rx_bytes_per_second": 999_999, "tx_bytes_per_second": 512},
                {"name": "wg0", "physical": False, "rx_bytes_per_second": 5, "tx_bytes_per_second": 5},
            ],
        },
        "disk_io": {
            "read_bytes_per_second": 2048,
            "write_bytes_per_second": 99_999,
            "busy_percent": 98,
            "devices": [{"name": "sdb", "read_bytes_per_second": 2048, "write_bytes_per_second": 99_999, "busy_percent": 98}],
        },
        "thermal_sensors": [
            {"zone": "thermal_zone0", "type": "cpu-thermal", "cpu_ids": [0, 1, 2, 3], "shared": True, "temperature_c": 79}
        ],
        "top_cpu": [{"name": "python3", "pid": 4, "cpu_percent": 80}],
        "top_memory": [{"name": "hass", "pid": 200, "rss_bytes": 300_000_000}],
        "throttle": {"raw": 0x50005, "hex": "0x50005", "current": ["under_voltage", "throttled"], "occurred": ["under_voltage", "throttled"]},
    }


def _crash_analysis(boot_id, analyzed_at, headline, count):
    return {
        "ok": True,
        "generated_at": analyzed_at,
        "analysis": {
            "available": True,
            "level": "warning",
            "headline": headline,
            "findings": [headline],
            "counts": {"undervoltage_started": count},
            "timeline": [{"timestamp": analyzed_at - 1, "message": headline}],
            "pstore": [],
            "previous_boot": {"boot_id": boot_id, "started_at": analyzed_at - 100, "ended_at": analyzed_at - 10},
        },
    }


def _populate_crash_and_samples(store):
    store.save_crash_analysis(_crash_analysis("boot-old", NOW - 7200, "Abrupt restart", 3))
    store.save_crash_analysis(_crash_analysis("boot-older", NOW - 9000, "Clean shutdown", 0))
    store.record_sample({"timestamp": NOW - 5, "uptime_seconds": 20, "boot_id": "boot-one", "cpu_percent": 2})
    for index in range(4):
        store.record_sample({
            "timestamp": NOW - 4000 + index * 5,
            "uptime_seconds": 900 + index * 5,
            "boot_id": PREVIOUS_BOOT,
            "cpu_percent": 20 + index * 20,
            "memory": {"used_percent": 50 + index * 10, "available_bytes": 1000 - index},
            "swap": {"used_percent": index},
            "load": {"1m": index},
            "temperature_c": 60 + index,
            "arm_mhz": 1500 - index * 100,
            "disk_io": {"busy_percent": index * 30, "read_bytes_per_second": index, "write_bytes_per_second": index * 2},
            "network_io": {"rx_bytes_per_second": index * 3, "tx_bytes_per_second": index * 4},
            "top_cpu": [{"name": f"proc{index}", "cpu_percent": 10 * index}],
            "top_memory": [{"name": f"mem{index}", "rss_bytes": 100 * index}],
        })
    store.set_meta("last_boot_id", PREVIOUS_BOOT)


def populate(database):
    store = EventStore(database, clock=lambda: NOW)
    _populate_events(store)
    _populate_rollups(store)
    store.set_meta("current", _current_snapshot())
    _populate_crash_and_samples(store)
    store.close()


class SystemMonitorSnapshotTests(unittest.TestCase):
    def stage(self, root):
        stage = root / "scripts"
        stage.mkdir()
        source_root = Path(__file__).resolve().parents[1] / "scripts"
        shutil.copy2(source_root / "system_event_monitor.py", stage / "system_event_monitor.py")
        shutil.copytree(
            source_root / "system_monitor",
            stage / "system_monitor",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        shutil.copytree(
            source_root / "network_recorder",
            stage / "network_recorder",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        runner = root / "runner.py"
        runner.write_text(_CHILD_RUNNER, encoding="utf-8")
        return stage, runner

    def run_child(self, root, runner, *args):
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["EXPECTED_CWD"] = str(root)
        env["EXPECTED_REDACTED"] = package_parsers.redact(PASSWORD_SAMPLE)
        return subprocess.run(
            [sys.executable, "-B", str(runner), *map(str, args)],
            cwd=root,
            env=env,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_deploy_shaped_snapshot_matches_fixtures(self):
        package_redacted = package_parsers.redact(PASSWORD_SAMPLE)
        self.assertNotIn("hunter2", package_redacted)

        before_modules = dict(sys.modules)
        with tempfile.TemporaryDirectory(prefix="system-monitor-snapshot-") as tempdir:
            root = Path(tempdir).resolve()
            stage, runner = self.stage(root)
            parser_process = self.run_child(root, runner, "parser", stage / "system_event_monitor.py")
            self.assertEqual(parser_process.returncode, 0, parser_process.stderr.decode())
            self.assertEqual(parser_process.stdout, b"")
            self.assertEqual(parser_process.stderr, b"")

            database = root / "events.sqlite3"
            populate(str(database))
            results = {}
            all_commands = list(COMMANDS.items()) + list(JOURNAL_COMMANDS.items())
            self.assertEqual(len(all_commands), 16)
            for name, args in all_commands:
                process = self.run_child(
                    root,
                    runner,
                    "cli",
                    stage / "system_event_monitor.py",
                    database,
                    "1" if name.startswith("journal_") else "0",
                    *args,
                )
                results[name] = (process.returncode, process.stdout, process.stderr)

            report_code, report_stdout, report_stderr = results["report_dashboard"]
            self.assertEqual(report_code, 0, report_stderr.decode())
            self.assertEqual(report_stderr, b"")
            self.assertEqual(
                report_stdout,
                (FIXTURES / "sysmon-report-before.json").read_bytes(),
            )

            extras_text = io.StringIO()
            json.dump(
                {
                    name: {
                        "exit": code,
                        "stdout": stdout.decode(),
                        "stderr": stderr.decode(),
                    }
                    for name, (code, stdout, stderr) in results.items()
                },
                extras_text,
                indent=2,
                sort_keys=False,
            )
            extras_text.write("\n")
            self.assertEqual(
                extras_text.getvalue().encode(),
                (FIXTURES / "sysmon-extras-before.json").read_bytes(),
            )

            staged_prefix = str(stage) + os.sep
            for name, module in sys.modules.items():
                module_path = getattr(module, "__file__", None)
                self.assertFalse(
                    module_path and os.path.abspath(module_path).startswith(staged_prefix),
                    name,
                )
            new_flat_modules = {
                name
                for name in set(sys.modules) - set(before_modules)
                if name == "system_monitor"
                or name.startswith("system_monitor.")
                or name == "network_recorder"
                or name.startswith("network_recorder.")
                or name == "system_event_monitor"
            }
            self.assertEqual(new_flat_modules, set())


if __name__ == "__main__":
    unittest.main()
