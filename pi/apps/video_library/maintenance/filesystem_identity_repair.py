#!/usr/bin/env python3
"""Guarded Silo identity and device-churn legacy-key repair.

Capture version-2 reviewed evidence with --capture-plan. Repair runs require it,
default to an in-memory dry run, and never modify media or torrent state.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

from pi.apps.video_library.catalog import MediaAssetCatalog
from pi.apps.video_library.catalog_values import CatalogConflict
from pi.apps.video_library.file_observations import matching_file_assets, record_file_identity
from pi.apps.video_library.library import MediaLibrary, default_sources
from pi.apps.video_library.maintenance import identity_repair_evidence as evidence
from pi.apps.video_library.maintenance import legacy_churn_evidence, legacy_churn_repair
from pi.apps.video_library.maintenance.same_file_repair import (
    ChangedEvidence,
    PairStatus,
    digest,
    ensure_unused,
    one,
    repair_timestamp,
    rows,
    snapshot,
)
from pi.apps.video_library.schema import SCHEMA_VERSION

PREFIX = "video_v2_"
REASON = "reviewed Silo filesystem-identity repair"


def _schema_check(db: sqlite3.Connection) -> None:
    versions = {row[0] for row in db.execute("SELECT version FROM video_v2_schema_migrations")}
    if versions != set(range(1, SCHEMA_VERSION + 1)):
        raise RuntimeError("schema must already match deployed catalog; migration refused")


def _new_changes() -> dict[str, defaultdict[str, set[tuple[Any, ...]]]]:
    return {"inserted": defaultdict(set), "updated": defaultdict(set)}


def _mark(changes, kind, table, *key) -> None:
    changes[kind][table].add(tuple(key))


def _invalidate_wrong_identities(db, runtime, changes):
    invalidated = []
    for old in runtime["identities"]["wrong_target"]:
        if old["device_id"] is None:
            continue
        changed = db.execute("""
            UPDATE video_v2_file_identities SET device_id=NULL, inode=NULL
            WHERE identity_id=? AND asset_id=? AND device_id=? AND inode=?
              AND size=? AND mtime_ns=? AND fingerprint_algorithm IS ? AND fingerprint IS ?
            """, tuple(old[key] for key in (
                "identity_id", "asset_id", "device_id", "inode", "size", "mtime_ns",
                "fingerprint_algorithm", "fingerprint")))
        if changed.rowcount != 1:
            raise RuntimeError("guarded identity invalidation did not update exactly one row")
        _mark(changes, "updated", PREFIX + "file_identities", old["identity_id"])
        invalidated.append(old["identity_id"])
    return invalidated


def _ensure_canonical_identity(catalog, runtime, timestamp, changes):
    if runtime["identities"]["canonical_stable"] is not None:
        return None
    db = catalog.connection
    before = {row[0] for row in db.execute("SELECT identity_id FROM video_v2_file_identities")}
    target = runtime["target"]
    record_file_identity(
        catalog, db, asset_id=evidence.CORRECT_ASSET,
        device_id=target["device_id"], inode=target["inode"], size=target["size"],
        mtime_ns=target["mtime_ns"], fingerprint_algorithm=None, fingerprint=None,
        observed_at=timestamp,
    )
    after = {row[0] for row in db.execute("SELECT identity_id FROM video_v2_file_identities")}
    inserted = after - before
    if len(inserted) != 1:
        raise RuntimeError("canonical identity insertion did not add exactly one row")
    identity_id = inserted.pop()
    _mark(changes, "inserted", PREFIX + "file_identities", identity_id)
    return identity_id


def _version_locations(catalog, runtime, timestamp, changes):
    if runtime["observations"]["location_status"] == evidence.RepairPhase.DONE:
        return []
    inserted = []
    for old in runtime["observations"]["active_locations"]:
        _mark(changes, "updated", PREFIX + "locations", old["location_id"])
        location_id = catalog.record_location(
            evidence.CORRECT_ASSET, old["path"], location_kind=old["location_kind"],
            source=old["source"], metadata=json.loads(old["metadata_json"] or "null"),
            observed_at=timestamp, connection=catalog.connection,
        )
        _mark(changes, "inserted", PREFIX + "locations", location_id)
        inserted.append(location_id)
    return inserted


def _version_aliases(catalog, runtime, timestamp, changes):
    if runtime["observations"]["alias_status"] == evidence.RepairPhase.DONE:
        return []
    inserted = []
    for old in runtime["observations"]["active_aliases"]:
        _mark(changes, "updated", PREFIX + "aliases", old["alias_id"])
        alias_id = catalog.record_alias(
            evidence.CORRECT_ASSET, old["alias"], namespace=old["namespace"],
            provenance=old["provenance"], metadata=json.loads(old["metadata_json"] or "null"),
            observed_at=timestamp, connection=catalog.connection,
        )
        _mark(changes, "inserted", PREFIX + "aliases", alias_id)
        inserted.append(alias_id)
    return inserted


def _rebind_legacy(catalog, runtime, timestamp, changes):
    if runtime["legacy_status"] == evidence.RepairPhase.DONE:
        return False
    old = runtime["legacy"]
    _mark(changes, "updated", PREFIX + "legacy_keys", old["source"], old["media_key"])
    catalog.bind_legacy_key(
        evidence.CORRECT_ASSET, old["media_key"], source=old["source"], replace=True,
        metadata=json.loads(old["metadata_json"] or "null"), observed_at=timestamp,
        connection=catalog.connection,
    )
    return True


def _verify_deltas(db, before, changes, report):
    after = snapshot(db)
    if before.keys() != after.keys():
        raise RuntimeError("schema tables changed")
    report["row_counts"] = {}
    for table, old in before.items():
        new = after[table]
        removed = old.keys() - new.keys()
        inserted = new.keys() - old.keys()
        modified = {key for key in old.keys() & new.keys() if old[key] != new[key]}
        report["row_counts"][table] = {
            "before": len(old), "after": len(new), "delta": len(new) - len(old),
            "modified": len(modified), "deleted": len(removed),
        }
        if removed:
            raise RuntimeError(f"row deletion is forbidden: {table}")
        if inserted != changes["inserted"][table]:
            raise RuntimeError(f"unexpected inserted rows: {table}")
        if modified != changes["updated"][table]:
            raise RuntimeError(f"unexpected modified rows: {table}")
    return after


def _scan_item(catalog, db, item):
    observation = evidence.filesystem_observation(item.real_path)
    candidates = matching_file_assets(
        catalog, db, path=item.real_path, device_id=observation["device_id"],
        inode=observation["inode"], size=observation["size"],
        mtime_ns=observation["mtime_ns"], fingerprint_algorithm=None, fingerprint=None,
        preferred_asset_id=None,
    )
    return observation, candidates


def read_only_matcher_scan(db, library, catalog):
    """Run the deployed matcher without recording any scan observations."""
    if not library.scan():
        raise RuntimeError(f"library unavailable: {library.error}")
    items, shows = library.snapshot()
    result = {"items": len(items), "shows": len(shows), "conflicts": [],
              "stat_errors": [], "unresolved": [], "legacy_binding_mismatches": [],
              "target_resolutions": []}
    for item in items:
        try:
            observation, candidates = _scan_item(catalog, db, item)
        except CatalogConflict as exc:
            result["conflicts"].append({"media_key": item.key, "path": item.path,
                                        "real_path": item.real_path, "error": str(exc)})
            continue
        except OSError as exc:
            result["stat_errors"].append({"media_key": item.key, "path": item.path,
                                          "real_path": item.real_path, "error": str(exc)})
            continue
        candidate = next(iter(candidates), None)
        if candidate is None:
            result["unresolved"].append({"media_key": item.key, "path": item.path,
                                         "real_path": item.real_path,
                                         "device_id": observation["device_id"]})
        legacy = one(db, PREFIX + "legacy_keys", "WHERE source=? AND media_key=?",
                     ("v1-progress", item.key))
        if candidate is not None and legacy is not None and legacy["asset_id"] != candidate:
            result["legacy_binding_mismatches"].append({
                "media_key": item.key, "path": item.path, "real_path": item.real_path,
                "bound_asset": legacy["asset_id"], "current_asset": candidate,
            })
        if item.real_path == evidence.TARGET_PATH:
            result["target_resolutions"].append({"media_key": item.key, "path": item.path,
                                                  "asset_id": candidate})
    result["conflict_count"] = len(result["conflicts"])
    result["stat_error_count"] = len(result["stat_errors"])
    result["unresolved_count"] = len(result["unresolved"])
    result["legacy_mismatch_count"] = len(result["legacy_binding_mismatches"])
    degraded = any(result[key] for key in ("conflicts", "stat_errors", "unresolved",
                                            "legacy_binding_mismatches"))
    result["catalog_status"] = "remaining mismatches or scan errors" if degraded else "clear"
    return result


def _library_membership(library):
    items, _ = library.snapshot()
    return digest(sorted((item.key, item.path, item.real_path) for item in items))


def _verify_postconditions(db, runtime, plan, before, changes, report, library, catalog):
    final = evidence.validate_against_plan(db, plan)
    if final["identities"]["wrong_status"] != evidence.RepairPhase.DONE:
        raise RuntimeError("wrong Silo physical identity remains active")
    if final["identities"]["canonical_stable"] is None:
        raise RuntimeError("canonical stable identity missing")
    for key in ("location_status", "alias_status"):
        if final["observations"][key] != evidence.RepairPhase.DONE:
            raise RuntimeError(f"post-repair {key} is not canonical")
    if final["legacy_status"] != evidence.RepairPhase.DONE:
        raise RuntimeError("legacy key was not rebound")
    integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
    foreign_keys = [list(row) for row in db.execute("PRAGMA foreign_key_check")]
    report["integrity_check"], report["foreign_key_check"] = integrity, foreign_keys
    if integrity != ["ok"] or foreign_keys:
        raise RuntimeError("integrity/foreign-key check failed")
    scan = read_only_matcher_scan(db, library, catalog)
    _verify_deltas(db, before, changes, report)
    report["scan_after"] = scan
    if scan["conflicts"] or scan["stat_errors"] or scan["unresolved"]:
        raise RuntimeError("post-repair file conflicts, stat errors or unresolved identities remain")
    if scan["legacy_binding_mismatches"]:
        raise RuntimeError("post-repair legacy binding mismatches remain")
    legacy_churn_repair.verify_legacy_repair(catalog, plan["legacy_churn"], library)
    if _library_membership(library) != report["library_membership_before"]:
        raise RuntimeError("library membership changed during repair")
    targets = scan["target_resolutions"]
    if not targets or any(row["asset_id"] != evidence.CORRECT_ASSET for row in targets):
        raise RuntimeError("new matcher did not resolve Silo target to canonical asset")
    return final


def _apply_steps(catalog, runtime, timestamp, changes):
    return {
        "invalidated_identity_ids": _invalidate_wrong_identities(
            catalog.connection, runtime, changes),
        "canonical_identity_inserted": _ensure_canonical_identity(
            catalog, runtime, timestamp, changes),
        "canonical_location_ids": _version_locations(catalog, runtime, timestamp, changes),
        "canonical_alias_ids": _version_aliases(catalog, runtime, timestamp, changes),
        "legacy_rebound": _rebind_legacy(catalog, runtime, timestamp, changes),
    }


def run_repair(db, plan, report, library, apply, prior):
    db.row_factory = sqlite3.Row
    _schema_check(db)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        before = snapshot(db)
        if not library.scan():
            raise RuntimeError(f"library unavailable: {library.error}")
        report["library_membership_before"] = _library_membership(library)
        runtime = evidence.validate_against_plan(db, plan)
        timestamp = repair_timestamp(db)
        catalog = MediaAssetCatalog(connection=db, clock=lambda: timestamp)
        changes = _new_changes()
        report["repair_timestamp"] = timestamp
        report["repair"] = _apply_steps(catalog, runtime, timestamp, changes)
        legacy_churn_repair.run_legacy_repair(
            catalog, plan["legacy_churn"], prior, library, changes, report)
        final = _verify_postconditions(
            db, runtime, plan, before, changes, report, library, catalog)
        report["counterpart_duplicates"] = final["counterpart_duplicates"]
        report["preserved"] = {
            "audio_playback_state": final["playback"]["states"],
            "audio_sessions": len(final["playback"]["sessions"]),
            "audio_events": len(final["playback"]["events"]),
            "old_barbarians_identity": next(
                row for row in final["identities"]["rows"]
                if row["identity_id"] == evidence.OLD_BARBARIANS_IDENTITY),
            "deletions": 0,
        }
        report["sqlite_changes"] = db.total_changes
        changed = any(changes[kind][table] for kind in changes for table in changes[kind])
        report["status"] = PairStatus.READY if changed else PairStatus.DONE
        if apply:
            db.commit()
            report["committed"] = True
        else:
            db.rollback()
            report["rolled_back"] = True
    except (ChangedEvidence, OSError) as exc:
        db.rollback()
        report.update(status=PairStatus.SKIPPED, skipped_reason=str(exc), rolled_back=True,
                      sqlite_changes=db.total_changes)
    except BaseException:
        db.rollback()
        report["rolled_back"] = True
        raise


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    os.umask(0o077)
    with path.open("x") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def capture_plan(db_path: Path, output_path: Path, prior_path: Path) -> int:
    prior = legacy_churn_evidence.load_prior_plan(prior_path)
    library = MediaLibrary(default_sources())
    if not library.scan():
        raise RuntimeError(f"library unavailable: {library.error}")
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        _schema_check(db)
        db.execute("BEGIN")
        try:
            plan = evidence.build_plan(db)
            # The matcher takes its read connection explicitly; no catalog migration
            # or writer reservation is performed against the captured database.
            with MediaAssetCatalog(":memory:") as catalog:
                plan["legacy_churn"] = legacy_churn_evidence.capture_churn_plan(
                    db, library, catalog, prior)
        finally:
            db.rollback()
    _write_json_exclusive(output_path, plan)
    print(json.dumps({"captured": True, "plan": str(output_path),
                      "repair": plan["repair"]}))
    return 0


def _repair_run(args, plan, report):
    prior = legacy_churn_evidence.load_prior_plan(args.prior_repair_plan)
    legacy_churn_evidence.validate_churn_plan(plan.get("legacy_churn"), prior)
    mode = "rw" if args.apply else "ro"
    with closing(sqlite3.connect(args.db.as_uri() + f"?mode={mode}", uri=True, timeout=5)) as source:
        library = MediaLibrary(default_sources())
        if args.apply:
            run_repair(source, plan, report, library, True, prior)
        else:
            with closing(sqlite3.connect(":memory:")) as memory:
                source.backup(memory)
                run_repair(memory, plan, report, library, False, prior)


def _run_with_report(args) -> int:
    started = time.monotonic()
    report = {"db": str(args.db), "plan": str(args.plan), "apply": args.apply,
              "committed": False, "started_at": time.time()}
    code = 1
    with args.report.open("x") as output:
        try:
            plan_bytes = args.plan.read_bytes()
            plan = json.loads(plan_bytes)
            report["plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
            evidence.validate_plan_shape(plan)
            if args.apply:
                report["quiescence"] = ensure_unused(args.db)
            _repair_run(args, plan, report)
            code = 2 if report.get("status") == PairStatus.SKIPPED else 0
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            report["runtime_seconds"] = round(time.monotonic() - started, 3)
            report["summary"] = {"status": report.get("status", "error"),
                                 "skipped": int(report.get("status") == PairStatus.SKIPPED),
                                 "committed": report["committed"]}
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    print(json.dumps(report["summary"]))
    if report.get("error"):
        print(report["error"])
    if report.get("skipped_reason"):
        print(f"Skipped: {report['skipped_reason']}")
    return code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--prior-repair-plan", type=Path,
                        default=legacy_churn_evidence.PRIOR_PLAN_PATH)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture-plan", type=Path, metavar="OUTPUT")
    source.add_argument("--plan", type=Path)
    parser.add_argument("--report", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default; repair a memory copy")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--i-have-stopped-video-library", action="store_true")
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    args.db = args.db.resolve(strict=True)
    if args.capture_plan:
        if args.report or args.apply or args.i_have_stopped_video_library:
            parser.error("--capture-plan does not accept repair/apply options")
        output = args.capture_plan.resolve()
        if output == args.db:
            parser.error("plan must not overwrite the database")
        try:
            return capture_plan(args.db, output, args.prior_repair_plan)
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}")
            return 1
    if args.report is None:
        parser.error("--plan requires --report")
    args.plan = args.plan.resolve(strict=True)
    args.report = args.report.resolve()
    if args.apply and not args.i_have_stopped_video_library:
        parser.error("--apply requires --i-have-stopped-video-library")
    if args.report in (args.db, args.plan):
        parser.error("report must not overwrite the database or plan")
    os.umask(0o077)
    return _run_with_report(args)


if __name__ == "__main__":
    raise SystemExit(main())
