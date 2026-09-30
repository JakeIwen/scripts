"""Independent removable-storage safety and continuity acceptance tests.

All filesystems/devices are isolated fixtures; these tests never mount, unmount,
probe, or change a live device. Mount identity is injected only at its guard.
"""

import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock
import datetime as dt
import errno
import stat
from types import SimpleNamespace
import importlib.util
import io
import sys

from pi.scripts.network_recorder import export_incident, parse_syslog, report
from pi.scripts.network_recorder.collector import FileImporter


PI_ROOT = Path(__file__).resolve().parents[2]
BASE = dt.datetime(2026, 9, 30, 12, tzinfo=dt.timezone.utc).timestamp()


def line(number, failure=False, padding=""):
    timestamp = dt.datetime.fromtimestamp(BASE + number, dt.timezone.utc).isoformat()
    return (f"{timestamp} 192.168.6.1 OpenWrt clientwan-path: state-change "
            f"state=gateway-1-public-{0 if failure else 2} device=wl1-sta0 "
            f"gateway=172.20.10.1 gateway_ok=1 public1=1.1.1.1:{0 if failure else 1} "
            f"public2=208.67.222.222:{0 if failure else 1}" + padding + "\n")


class FixtureGuard:
    """Only the mount-verification boundary is replaced by a fixture."""

    def __init__(self, error_type):
        self.error_type = error_type
        self.available = False
        self.ram_available = True
        self.token = ("fixture-exfat", 10, "8:33")
        self.reason = "fixture flash missing"

    def flash(self):
        if not self.available:
            raise self.error_type(self.reason)
        return self.token

    def ram(self):
        if not self.ram_available:
            raise self.error_type("fixture runtime is not tmpfs")
        return ("fixture-tmpfs", 2, "0:44")


class RecorderStorageTests(unittest.TestCase):
    def setUp(self):
        from pi.scripts.network_recorder import storage
        self.storage = storage
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        mountpoint = self.root / "mnt" / "EXFAT512"
        mountpoint.mkdir(parents=True)
        runtime = self.root / "run" / "vanpi-network"
        runtime.mkdir(parents=True)
        self.config = storage.StorageConfig(mountpoint=mountpoint, uuid="1234-ABCD",
                                           flash_root=mountpoint / "vanpi-network", runtime_root=runtime)
        self.guard = FixtureGuard(storage.StorageUnavailable)
        self.manager = storage.StorageManager(self.config, guard=self.guard)
        self.addCleanup(lambda: self.manager.close())
        self.clock = mock.patch.object(storage.time, "time", return_value=BASE + 1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.monotonic = 1000
        steady = mock.patch.object(storage.time, "monotonic", side_effect=lambda:self.monotonic)
        steady.start()
        self.addCleanup(steady.stop)

    def event(self, number, failure=False, padding=""):
        return parse_syslog(line(number, failure, padding), imported_at=BASE + 1000,
                            provenance={"generation": "storage-fixture", "offset": number}, backfill=True)

    def data(self, path=None):
        return report(str(path or self.manager.store.path), BASE - 1, BASE + 20000,
                      now=BASE + 1000, limit=500)

    def to_flash(self):
        self.guard.available = True
        for _ in range(40):
            self.monotonic += 10  # Advance the retry interval without real waits.
            self.manager.tick()
            if self.manager.mode == "flash":
                return
        self.fail("bounded RAM drain did not complete within 40 ticks")

    def test_missing_flash_uses_ram_without_creating_underlying_sd_directory(self):
        store = self.manager.open()
        self.assertEqual(self.manager.mode, "ram")
        self.assertEqual(Path(store.path), self.config.ram_database)
        self.assertFalse(self.config.flash_root.exists())
        store.ingest([self.event(10, failure=True)])
        self.manager.record_coverage()
        self.assertFalse(self.config.flash_root.exists())
        self.assertEqual(self.data()["counts"]["events"], 1)
        coverage = next(row for row in self.data()["coverage"] if row["source"] == "storage")
        self.assertIn("volatile", json.dumps(coverage).lower())
        self.assertIn("history", json.dumps(coverage).lower())

    def test_invalid_runtime_and_absent_flash_fail_without_persistent_fallback(self):
        self.guard.ram_available = False
        with self.assertRaises(self.storage.StorageUnavailable):
            self.manager.open()
        self.assertFalse(self.config.flash_root.exists())
        self.assertFalse(self.config.ram_database.exists())

    def test_flash_loss_closes_old_writer_and_next_observation_goes_to_ram(self):
        self.guard.available = True
        flash = self.manager.open()
        flash.ingest([self.event(10, failure=True)])
        self.guard.available = False
        ram = self.manager.tick()
        self.assertEqual(self.manager.mode, "ram")
        self.assertIsNone(flash.db)
        self.assertEqual(Path(ram.path), self.config.ram_database)
        ram.ingest([self.event(20)])
        self.assertEqual(self.data(self.config.flash_database)["counts"]["events"], 1)
        self.assertEqual(self.data()["counts"]["events"], 1)

    def test_changed_verified_mount_identity_reopens_instead_of_reusing_writer(self):
        self.guard.available = True
        original = self.manager.open()
        original.ingest([self.event(10, failure=True)])
        self.guard.token = ("fixture-exfat", 11, "8:49")
        self.manager.tick()
        self.assertIsNone(original.db)
        self.assertIsNot(self.manager.store, original)
        self.to_flash()
        self.assertEqual(Path(self.manager.store.path), self.config.flash_database)

    def test_writer_rechecks_mount_identity_before_mutation_without_tick(self):
        self.guard.available = True
        writer = self.manager.open()
        writer.ingest([self.event(1)])
        self.guard.token = ("replacement", 77, "8:49")
        with self.assertRaises(self.storage.StorageUnavailable):
            writer.ingest([self.event(2)])
        self.assertEqual(self.data()["counts"]["events"], 1)

    def mount_guard(self):
        guard = self.storage.MountGuard(self.config)
        device = os.makedev(8, 33)
        rows = [dict(id="10", mountpoint=str(self.config.mountpoint), fstype="exfat",
                     source="/dev/fixture1", major=8, minor=33, options={"rw"}),
                dict(id="20", mountpoint=str(self.config.runtime_root.parent), fstype="tmpfs",
                     source="tmpfs", major=0, minor=44, options={"rw"})]
        patches = [mock.patch.object(guard, "_mount_rows", return_value=rows),
                   mock.patch.object(guard, "_uuid_device", return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=device)),
                   mock.patch.object(guard, "_path_stat", return_value=SimpleNamespace(st_dev=device))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return guard, rows

    def test_mount_guard_verifies_uuid_device_fstype_and_mount_generation(self):
        guard, rows = self.mount_guard()
        first = guard.flash()
        guard.ram()
        rows[0]["id"] = "11"
        self.assertNotEqual(guard.flash(), first)
        with mock.patch.object(guard, "_uuid_device", return_value=SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(8, 49))):
            with self.assertRaises(self.storage.StorageUnavailable):
                guard.flash()
        rows[0]["fstype"] = "ext4"
        with self.assertRaises(self.storage.StorageUnavailable):
            guard.flash()
        self.assertFalse(self.config.flash_root.exists())

    def test_mount_guard_rejects_absent_ambiguous_readonly_and_nested_mounts(self):
        guard, rows = self.mount_guard()
        for variant in ([], [dict(rows[0]), dict(rows[0])],
                        [dict(rows[0], options={"ro"})],
                        [dict(rows[0]), dict(rows[0], id="99", mountpoint=str(self.config.flash_logs))]):
            with self.subTest(variant=variant), mock.patch.object(guard, "_mount_rows", return_value=variant):
                with self.assertRaises(self.storage.StorageUnavailable):
                    guard.flash()
        self.assertFalse(self.config.flash_root.exists())

    def test_discovery_failure_never_becomes_successful_missing_mount_guess(self):
        guard, _ = self.mount_guard()
        with mock.patch.object(guard, "_uuid_device", side_effect=FileNotFoundError("fixture missing UUID")):
            with self.assertRaises(self.storage.StorageUnavailable):
                guard.flash()
        with mock.patch.object(guard, "_mount_rows", side_effect=PermissionError("fixture mount discovery failed")):
            with self.assertRaises(self.storage.StorageUnavailable):
                guard.flash()

    def test_runtime_guard_rejects_non_tmpfs_readonly_and_symlink_paths(self):
        guard, rows = self.mount_guard()
        for filesystem, options in (("ext4", {"rw"}), ("tmpfs", {"ro"})):
            rows[1].update(fstype=filesystem, options=options)
            with self.assertRaises(self.storage.StorageUnavailable):
                guard.ram()
        rows[1].update(fstype="tmpfs", options={"rw"})
        self.config.ram_database.symlink_to(self.root / "unexpected.sqlite3")
        with self.assertRaises(self.storage.StorageUnavailable):
            guard.ram()

    def test_exfat_unsupported_chmod_is_tolerated_but_io_error_switches_to_ram(self):
        self.guard.available = True
        real_chmod = os.chmod

        def chmod(path, mode, *args, **kwargs):
            if Path(path) == self.config.flash_database:
                raise OSError(errno.EOPNOTSUPP, "fixture exfat does not support chmod")
            return real_chmod(path, mode, *args, **kwargs)

        with mock.patch("pi.scripts.network_recorder.store.os.chmod", side_effect=chmod):
            self.manager.open()
        self.assertEqual(self.manager.mode, "flash")
        self.manager.close()
        self.manager = self.storage.StorageManager(self.config, guard=self.guard)

        def io_error(path, mode, *args, **kwargs):
            if Path(path) == self.config.flash_database:
                raise OSError(errno.EIO, "fixture device I/O error")
            return real_chmod(path, mode, *args, **kwargs)

        with mock.patch("pi.scripts.network_recorder.store.os.chmod", side_effect=io_error):
            self.manager.open()
        self.assertEqual(self.manager.mode, "ram")
        self.manager.store.ingest([self.event(1)])
        self.assertEqual(self.data()["counts"]["events"], 1)

    def config_file(self):
        values = {key: str(value) if isinstance(value, Path) else value
                  for key, value in vars(self.config).items()}
        values["schema_version"] = 1
        path = self.root / "storage.json"
        path.write_text(json.dumps(values))
        return path

    def load_cli(self):
        spec = importlib.util.spec_from_file_location("storage_cli_fixture", PI_ROOT / "scripts/network_flight_recorder.py")
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "path", [str(PI_ROOT / "scripts")] + sys.path):
            spec.loader.exec_module(module)
        return module

    def test_auto_cli_writer_never_creates_flash_underlay_after_selector_race(self):
        cli = self.load_cli()
        config_path = self.config_file()
        cli_storage = sys.modules[cli.run_storage.__module__]
        # The read selector resolved flash just before it disappeared. A writer
        # must independently verify storage, not trust that earlier path string.
        with mock.patch.object(cli, "resolve_database", return_value=str(self.config.flash_database)), \
                mock.patch.object(cli_storage, "MountGuard", return_value=self.guard), \
                mock.patch.object(cli, "load_config", return_value=self.config), \
                mock.patch("subprocess.run", side_effect=AssertionError("no live commands in storage fixture")), \
                mock.patch.object(sys, "stderr", new_callable=io.StringIO):
            result = cli.main(["--storage-config", str(config_path), "init"])
        self.assertIn(result, (0, 1))
        self.assertFalse(self.config.flash_root.exists())

    def test_auto_cli_init_keeps_ram_budget_instead_of_store_default(self):
        self.manager.open()
        self.manager.close()
        cli = self.load_cli()
        config_path = self.config_file()
        cli_storage = sys.modules[cli.run_storage.__module__]
        with mock.patch.object(cli, "resolve_database", return_value=str(self.config.ram_database)), \
                mock.patch.object(cli_storage, "MountGuard", return_value=self.guard), \
                mock.patch.object(cli, "load_config", return_value=self.config), \
                mock.patch("subprocess.run", side_effect=AssertionError("no live commands in storage fixture")):
            result = cli.main(["--storage-config", str(config_path), "init"])
        self.assertEqual(result, 0)
        from pi.scripts.network_recorder.store import connect_readonly
        database = connect_readonly(self.config.ram_database)
        try:
            limits = json.loads(database.execute("SELECT value FROM checkpoints WHERE key='limits'").fetchone()[0])
        finally:
            database.close()
        self.assertEqual(limits["max_bytes"], self.config.ram_max_bytes)

    def test_unconfigured_auto_fails_before_accessing_legacy_sd_database(self):
        cli = self.load_cli()
        cli_storage = sys.modules[cli.run_storage.__module__]
        missing = str(self.root / "missing-storage-config.json")
        environment = dict(os.environ)
        environment.pop("VANPI_NETWORK_STORAGE_CONFIG", None)
        environment.pop("VANPI_NETWORK_DATABASE", None)
        for command in ("init", "report"):
            with self.subTest(command=command), \
                    mock.patch.dict(os.environ, environment, clear=True), \
                    mock.patch.object(cli, "DEFAULT_CONFIG", missing), \
                    mock.patch.object(cli_storage, "DEFAULT_CONFIG", missing), \
                    mock.patch.object(cli, "Store", side_effect=AssertionError("unguarded legacy writer")) as writer, \
                    mock.patch.object(cli, "report", side_effect=AssertionError("unexpected legacy read")) as reader, \
                    mock.patch.object(sys, "stderr", new_callable=io.StringIO):
                self.assertEqual(cli.main([command]), 1)
                writer.assert_not_called()
                reader.assert_not_called()
        self.assertFalse(self.config.flash_root.exists())
        self.assertFalse(self.config.ram_database.exists())

    def test_read_selector_follows_ram_then_flash_and_rejects_unverified_flash(self):
        config_path = self.config_file()
        self.manager.open()
        with mock.patch.object(self.storage, "MountGuard", return_value=self.guard):
            self.assertEqual(self.storage.resolve_database(config_path=str(config_path)), str(self.config.ram_database))
            self.to_flash()
            self.assertEqual(self.storage.resolve_database(config_path=str(config_path)), str(self.config.flash_database))
            self.guard.available = False
            with self.assertRaises(self.storage.StorageUnavailable):
                self.storage.resolve_database(config_path=str(config_path))
            self.manager.tick()
            self.assertEqual(self.storage.resolve_database(config_path=str(config_path)), str(self.config.ram_database))

    def test_read_selector_rejects_forged_status_path_and_leaves_explicit_database_alone(self):
        config_path = self.config_file()
        self.manager.open()
        status = json.loads(self.config.status_path.read_text())
        status["active_database"] = str(self.root / "unrelated.sqlite3")
        self.config.status_path.write_text(json.dumps(status))
        with mock.patch.object(self.storage, "MountGuard", return_value=self.guard):
            with self.assertRaises(self.storage.StorageUnavailable):
                self.storage.resolve_database(config_path=str(config_path))
        custom = self.root / "intentional-fixture.sqlite3"
        self.assertEqual(self.storage.resolve_database(custom, config_path="missing"), str(custom))
        self.assertFalse(custom.exists())

    def test_ram_restart_then_flash_drain_preserves_times_provenance_and_counts(self):
        events = [self.event(10, failure=True), self.event(20)]
        self.manager.open().ingest(events)
        before = self.data()
        self.manager.close()
        self.manager = self.storage.StorageManager(self.config, guard=self.guard)
        self.manager.open()
        self.assertEqual(self.data()["counts"], before["counts"])
        self.to_flash()
        after = self.data()
        self.assertEqual(after["counts"], {"events": 2, "incidents": 1})
        for old, new in zip(sorted(before["events"], key=lambda e:e["time"]),
                            sorted(after["events"], key=lambda e:e["time"])):
            for key in ("time", "received_at", "source_time", "imported_at", "provenance", "session", "backfill"):
                self.assertEqual(new[key], old[key], key)
        self.assertEqual(after["incidents"][0]["duration_seconds"], 10)
        for _ in range(3):
            self.manager.tick()
        self.assertEqual(self.data()["counts"], {"events": 2, "incidents": 1})

    def test_ram_drain_spanning_multiple_batches_is_complete_before_flash_mode(self):
        self.manager.open().ingest([self.event(number) for number in range(1, 1002)])
        self.to_flash()
        self.assertEqual(self.data()["counts"]["events"], 1001)
        self.manager.close()
        self.manager = self.storage.StorageManager(self.config, guard=self.guard)
        self.manager.open()
        self.to_flash()
        self.assertEqual(self.data()["counts"]["events"], 1001)

    def test_second_usb_absence_collects_new_rows_after_previous_ram_acknowledgement(self):
        self.manager.open().ingest([self.event(10, failure=True), self.event(20)])
        inode = self.config.ram_database.stat().st_ino
        self.to_flash()
        self.assertEqual(self.config.ram_database.stat().st_ino, inode)
        self.manager.store.ingest([self.event(30, failure=True)])
        self.guard.available = False
        self.manager.tick().ingest([self.event(40)])
        self.assertEqual(self.manager.mode, "ram")
        self.to_flash()
        data = self.data()
        self.assertEqual(data["counts"], {"events": 4, "incidents": 2})
        self.assertEqual({e["time"] for e in data["events"]}, {BASE + 10, BASE + 20, BASE + 30, BASE + 40})

    def test_readonly_ram_report_remains_valid_during_flash_drain(self):
        from pi.scripts.network_recorder.store import connect_readonly
        self.manager.open().ingest([self.event(10, failure=True), self.event(20)])
        inode = self.config.ram_database.stat().st_ino
        reader = connect_readonly(self.config.ram_database)
        try:
            reader.execute("BEGIN")
            self.assertEqual(reader.execute("SELECT count(*) FROM events").fetchone()[0], 2)
            self.to_flash()
            self.assertEqual(reader.execute("SELECT count(*) FROM events").fetchone()[0], 2)
            self.assertEqual(self.config.ram_database.stat().st_ino, inode)
            self.assertEqual(self.data()["counts"], {"events": 2, "incidents": 1})
        finally:
            reader.close()

    def test_interrupted_partially_committed_ram_drain_replays_without_loss_or_duplicates(self):
        self.manager.open().ingest([self.event(number) for number in range(1, 1002)])
        factory = self.manager._flash_store
        interrupted = False

        def open_with_one_interruption(token):
            target = factory(token)
            bound_storage = target._bound_storage

            def after_committed_batch():
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    self.guard.available = False
                    raise self.storage.StorageUnavailable("fixture unplug after committed merge batch")
                return bound_storage()

            target._bound_storage = after_committed_batch
            return target

        self.guard.available = True
        self.monotonic += 10
        with mock.patch.object(self.manager, "_flash_store", side_effect=open_with_one_interruption):
            self.manager.tick()
        self.assertTrue(interrupted)
        self.assertEqual(self.manager.mode, "ram")
        self.assertEqual(self.data()["counts"]["events"], 1001)
        partial = self.data(self.config.flash_database)["counts"]["events"]
        self.assertGreater(partial, 0)
        self.assertLess(partial, 1001)
        self.to_flash()
        self.assertEqual(self.data()["counts"], {"events": 1001, "incidents": 0})
        self.manager.tick()
        self.assertEqual(self.data()["counts"]["events"], 1001)

    def test_ram_incident_link_resolves_to_containing_flash_episode_after_drain(self):
        self.guard.available = True
        self.manager.open().ingest([self.event(1), self.event(5, failure=True)])
        self.guard.available = False
        self.manager.tick().ingest([self.event(10, failure=True), self.event(20)])
        old_id = self.data()["incidents"][0]["id"]
        self.to_flash()
        bundle = export_incident(str(self.config.flash_database), old_id, now=BASE + 1000)
        self.assertEqual(bundle["incident"]["onset"], BASE + 5)
        self.assertEqual(bundle["incident"]["duration_seconds"], 15)
        self.assertNotEqual(bundle["incident"]["id"], old_id)

    def test_flash_drain_does_not_replace_flash_source_cursors_with_ram_cursors(self):
        self.guard.available = True
        self.manager.open().ingest([self.event(1)], checkpoints={
            "system-monitor-id": 4, "file-offset:fixture": {"offset": 400},
            "ubnt-snapshot": {"boot": "one", "log_digest": "earlier"},
        })
        self.guard.available = False
        self.manager.tick().ingest([self.event(2)], checkpoints={
            "system-monitor-id": 12, "file-offset:fixture": {"offset": 1200},
            "ubnt-snapshot": {"boot": "one", "log_digest": "latest"},
        })
        self.to_flash()
        self.assertEqual(self.manager.store.checkpoint("system-monitor-id"), 4)
        self.assertEqual(self.manager.store.checkpoint("file-offset:fixture"), {"offset": 400})
        self.assertEqual(self.manager.store.checkpoint("ubnt-snapshot")["log_digest"], "earlier")
        self.assertEqual(self.data()["counts"]["events"], 2)

    def import_mirrors(self):
        inserted = 0
        priority = self.manager.mirror_logs()
        archives = sorted(self.config.flash_logs.glob("*"))
        paths = list(dict.fromkeys([str(path) for path in priority] + [str(path) for path in archives]))
        for path in paths:
            inserted += FileImporter(self.manager.store).import_file(path)["inserted"]
        return inserted

    def test_ram_eviction_does_not_hide_records_still_recoverable_from_raw_spool(self):
        self.guard.available = True
        self.manager.open()
        self.config.spool_dir.mkdir(parents=True, exist_ok=True)
        raw = self.config.spool_dir / "dendelion.log"
        raw.write_text("".join(line(number, failure=number % 2 == 1) for number in range(1, 5)))
        self.import_mirrors()
        self.assertEqual(self.data()["counts"]["events"], 4)
        self.manager.tick()  # Cache the last healthy flash source cursors.

        self.guard.available = False
        ram = self.manager.tick()
        ram.max_events = 3  # Exercise bounded eviction without a large fixture.
        with raw.open("a") as handle:
            handle.write("".join(line(number, failure=number % 2 == 1) for number in range(5, 13)))
        FileImporter(ram).import_file(raw)
        self.assertEqual(self.data()["counts"]["events"], 3)
        self.assertEqual({e["time"] for e in self.data()["events"]}, {BASE + 10, BASE + 11, BASE + 12})

        self.to_flash()
        self.import_mirrors()
        restored = self.data()
        self.assertEqual(restored["counts"], {"events": 12, "incidents": 6})
        self.assertEqual({e["time"] for e in restored["events"]}, {BASE + number for number in range(1, 13)})
        self.assertTrue(all(e["received_at"] == e["time"] for e in restored["events"]))
        self.assertEqual(len({(e["provenance"]["generation"], e["provenance"]["offset"]) for e in restored["events"]}), 12)
        self.assertEqual(self.import_mirrors(), 0)
        self.assertEqual(self.data()["counts"], {"events": 12, "incidents": 6})

    def test_raw_rotation_and_partial_tail_survive_reconnect_without_duplicate_records(self):
        self.manager.open()
        self.config.spool_dir.mkdir(parents=True, exist_ok=True)
        raw = self.config.spool_dir / "dendelion.log"
        first = line(10, failure=True)
        second = line(20)
        raw.write_text(first + first + second.rstrip("\n"))
        FileImporter(self.manager.store).import_file(raw)
        self.assertEqual(self.data()["counts"]["events"], 2)
        with raw.open("a") as handle:
            handle.write("\n")
        raw.rename(raw.with_name("dendelion.log.1"))
        raw.write_text(line(30, failure=True) + line(40))
        self.to_flash()
        self.import_mirrors()
        data = self.data()
        self.assertEqual(data["counts"], {"events": 5, "incidents": 2})
        self.assertEqual({i["duration_seconds"] for i in data["incidents"]}, {10})
        self.assertEqual(self.import_mirrors(), 0)

    def test_ram_database_and_sidecars_stay_within_explicit_budget(self):
        store = self.manager.open()
        store.ingest([self.event(number, failure=True, padding="x" * 1100) for number in range(1, 4001)])
        store.prune(now=BASE + 1000)
        path = str(self.config.ram_database)
        size = sum(os.path.getsize(path + suffix) for suffix in ("", "-wal", "-shm") if os.path.exists(path + suffix))
        self.assertLessEqual(size, self.config.ram_max_bytes)
        data = self.data()
        self.assertGreater(data["counts"]["events"], 0)
        self.assertLess(data["counts"]["events"], 4000)


class StorageConfigurationTests(unittest.TestCase):
    def test_service_preserves_ram_and_prevents_old_sd_paths_becoming_fallback(self):
        content = (PI_ROOT / "services/network-flight-recorder.service").read_text()
        settings = {}
        for line in content.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                settings.setdefault(key, []).append(value)
        self.assertNotIn("StateDirectory", settings)
        self.assertEqual(settings["RuntimeDirectory"], ["vanpi-network"])
        self.assertEqual(settings["RuntimeDirectoryPreserve"], ["yes"])
        protected = " ".join(settings.get("ReadOnlyPaths", []))
        self.assertIn("/var/lib/vanpi-network", protected)
        self.assertIn("/var/log/openwrt", protected)
        self.assertNotIn("/mnt/EXFAT512", " ".join(settings.get("ReadWritePaths", [])))

    def test_tmpfiles_only_creates_ram_paths_before_rsyslog_starts(self):
        config = (PI_ROOT / "tmpfiles.d/vanpi-network.conf").read_text()
        rows = [line.split() for line in config.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual({row[1] for row in rows}, {"/run/vanpi-network", "/run/vanpi-network/spool"})
        self.assertTrue(all(row[0] == "d" for row in rows))
        self.assertTrue(all(row[1].startswith("/run/") for row in rows))
        dropin = (PI_ROOT / "services/rsyslog-vanpi-network.conf").read_text()
        self.assertIn("ExecStartPre=/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/vanpi-network.conf", dropin)

    def test_existing_borg_filesystem_boundary_excludes_ram_and_usb(self):
        script = (PI_ROOT / "scripts/backup/pi_backup.sh").read_text().replace("\\\n", "")
        command = re.search(r"^run borg create\b[^\n]*", script, re.MULTILINE)
        self.assertIsNotNone(command)
        self.assertIn("--one-file-system", command.group())
        self.assertRegex(command.group(), r'"::\$archive"\s+/\s+/boot/firmware\s*$')
        self.assertNotIn("/mnt/", command.group())
        self.assertNotIn("/run/", command.group())
        config = (PI_ROOT / "scripts/backup/backup_conf.sh").read_text()
        excluded = re.search(r"(?ms)^BORG_EXCLUDES=\(\n(.*?)^\)", config)
        self.assertIsNotNone(excluded)
        self.assertIn("'/var/lib/vanpi-network'", excluded.group(1))
        self.assertIn("'/var/log/openwrt'", excluded.group(1))


if __name__ == "__main__":
    unittest.main()
