"""Bound scan work without depending on machine-specific wall times."""

from __future__ import annotations

import unittest

from pi.tests.media.test_video_identity_integration import MediaFixture


class VideoScanPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = MediaFixture()
        self.addCleanup(self.fixture.cleanup)
        for index in range(4):
            payload = self.fixture.payload(f"Movie{index}.2026.mkv")
            self.fixture.link("Movies", payload.name, payload)
        self.service, self.library, self.store, self.catalog, _ = self.fixture.stack()

    def seed_positions(self, paths: list[str]) -> None:
        import_id = self.catalog.begin_import(
            source_kind="legacy-vlc-position-log", source_ref="positions.txt",
            source_digest="fixture"
        )
        for index, path in enumerate(paths):
            self.catalog.record_import(
                import_id,
                source_key=str(index),
                action="unresolved",
                source_updated=self.fixture.clock() + index,
                raw={"relative_path": path, "position_microseconds": (index + 1) * 1_000_000},
            )

    def test_rescan_reads_unresolved_history_once_not_once_per_item(self) -> None:
        self.seed_positions([f"/unavailable/{index}.mkv" for index in range(80)])
        queries: list[str] = []
        self.store.connection.set_trace_callback(queries.append)
        try:
            self.assertTrue(self.service.rescan())
        finally:
            self.store.connection.set_trace_callback(None)
        history_reads = [
            sql for sql in queries if "FROM video_v2_import_records AS records" in sql
        ]
        self.assertEqual(len(history_reads), 1)
        self.assertIsNone(self.service.identity_error)
        self.assertEqual(len(self.catalog.list_import_records(action="unresolved")), 80)

    def test_bound_files_use_indexed_identity_queries(self) -> None:
        queries: list[str] = []
        self.store.connection.set_trace_callback(queries.append)
        try:
            self.assertTrue(self.service.rescan())
        finally:
            self.store.connection.set_trace_callback(None)
        identity_queries = [
            sql for sql in queries if "FROM video_v2_file_identities" in sql
        ]
        self.assertEqual(len(identity_queries), 2 * len(self.library.snapshot()[0]))
        for sql in identity_queries:
            plan = self.store.connection.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            self.assertFalse(any("SCAN video_v2_file_identities" in row[3] for row in plan))

    def test_rescan_preserves_history_order_and_only_applies_matches_once(self) -> None:
        items, _ = self.library.snapshot()
        item = items[0]
        # Different evidence paths must retain the query's chronological order.
        self.seed_positions([item.rel_path, item.real_path, item.rel_path])
        self.assertTrue(self.service.rescan())
        state = self.catalog.get_asset_state(item.asset_id)
        self.assertEqual(state["position"], 3)
        records = self.catalog.list_import_records()
        matched = [row for row in records if row["source_kind"] == "legacy-vlc-position-log"]
        self.assertEqual([row["action"] for row in matched], ["applied"] * 3)
        self.assertEqual(self.catalog.list_import_records(action="unresolved"), [])
        events = self.catalog.list_events(item.asset_id)
        self.assertTrue(self.service.rescan())
        self.assertEqual(self.catalog.list_events(item.asset_id), events)

    def test_next_rescan_sees_history_added_after_previous_scan(self) -> None:
        items, _ = self.library.snapshot()
        self.seed_positions([items[0].real_path])
        self.assertTrue(self.service.rescan())
        self.assertEqual(self.catalog.get_asset_state(items[0].asset_id)["position"], 1)
        self.seed_positions([items[1].real_path])
        self.assertTrue(self.service.rescan())
        self.assertEqual(self.catalog.get_asset_state(items[1].asset_id)["position"], 1)


if __name__ == "__main__":
    unittest.main()
