#!/usr/bin/env python3
"""One-time reviewed repair; never import a server or mutate media.

Run this standalone file with PYTHONPATH pointing at the service's package
release. --plan is the version-1 enriched evidence JSON (original observations,
not a new discovery/approval). Dry runs write only an in-memory SQLite copy.
No migration, media hashing, implicit merge, service control or deletion occurs.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from types import SimpleNamespace

from pi.apps.video_library.catalog import MediaAssetCatalog
from pi.apps.video_library.legacy_progress import LegacyProgressMixin
from pi.apps.video_library.library import MediaLibrary, default_sources
from pi.apps.video_library.schema import SCHEMA_VERSION


PREFIX = "video_v2_"
REASON = "reviewed same-file catalog repair"


class PairStatus(str, Enum):
    READY = "approved"
    DONE = "already_repaired"
    SKIPPED = "skipped"


class ChangedEvidence(Exception):
    """Expected evidence no longer holds; do not repair this pair."""


def require(condition, message):
    if not condition:
        raise ChangedEvidence(message)


def rows(db, table, where="", args=()):
    return [dict(r) for r in db.execute(f'SELECT * FROM "{table}" {where}', args)]


def one(db, table, where, args):
    found = rows(db, table, where, args)
    if len(found) > 1:
        raise RuntimeError(f"ambiguous row in {table}: {args}")
    return found[0] if found else None


def stat_value(path):
    st = os.stat(path)
    return dict(device_id=str(st.st_dev), inode=st.st_ino, size=st.st_size,
                mtime_ns=st.st_mtime_ns, nlink=st.st_nlink)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def load_plan(path):
    plan = json.loads(path.read_text())
    if plan.get("repair_input_version") != 1:
        raise ValueError("use version-1 enriched original evidence, not raw discovery JSON")
    pairs = plan["same_file_pairs"]
    sources, targets = [p["source_asset"] for p in pairs], [p["target_asset"] for p in pairs]
    if len(set(sources)) != len(pairs) or len(set(targets)) != len(pairs):
        raise ValueError("duplicate pair assets")
    if set(sources) & set(targets):
        raise ValueError("source and target sets overlap")
    for p in pairs:
        for key in ("expected_assets", "expected_identities", "playback_states",
                    "locations_to_version", "aliases_to_version", "legacy_bindings_to_review"):
            if not isinstance(p[key], list):
                raise ValueError(f"{key} must be a list")
        if type(p["transfer_newer_playhead"]) is not bool:
            raise ValueError("explicit transfer decision required")
        if not p["locations_to_version"] or not p["aliases_to_version"]:
            raise ValueError("empty source observations")
    if not isinstance(plan["expected_library_items"], int):
        raise ValueError("expected library coverage is required")
    return plan


def ensure_unused(path):
    """Fail closed, conservatively rejecting readers as well as writers."""
    paths = [str(p) for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
             if p.exists()]
    command = shutil.which("fuser") or shutil.which("lsof")
    if command:
        args = [command, *paths] if Path(command).name == "fuser" else [command, "-t", "--", *paths]
        check = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if check.returncode != 1 or check.stdout.strip() or check.stderr.strip():
            raise RuntimeError(f"database in use or holder check inconclusive: {check.args}: "
                               f"{check.returncode} {check.stdout} {check.stderr}")
        return {"command": args, "result": "no holders"}
    command = shutil.which("systemctl")
    if not command:
        raise RuntimeError("no fuser/lsof/systemctl; cannot prove quiescence")
    check = subprocess.run([command, "is-active", "video-library"], capture_output=True,
                           text=True, timeout=30)
    if check.returncode != 3 or check.stdout.strip() != "inactive" or check.stderr.strip():
        raise RuntimeError("video-library is not confirmed inactive")
    return {"command": check.args, "result": "inactive"}


def scan(db, library):
    """The read-only candidate-set reproduction used in the investigation."""
    if not library.scan():
        raise RuntimeError(f"library unavailable: {library.error}")
    items, shows = library.snapshot()
    identities = rows(db, PREFIX + "file_identities")
    locations = {r["path"]: r for r in rows(db, PREFIX + "locations", "WHERE valid_to IS NULL")}
    by_asset, by_inode = defaultdict(list), defaultdict(set)
    for row in identities:
        if row["device_id"] is not None and row["inode"] is not None:
            by_asset[row["asset_id"]].append(row)
            by_inode[row["device_id"], row["inode"]].add(row["asset_id"])
    legacy = {r["media_key"]: r["asset_id"] for r in rows(
        db, PREFIX + "legacy_keys", "WHERE source='v1-progress'")}
    conflicts, errors, observations, legacy_mismatches = [], [], {}, []
    for item in items:
        try:
            st = stat_value(item.real_path)
            require(os.path.realpath(item.path) == item.real_path, "link retargeted")
        except (OSError, ChangedEvidence) as exc:
            errors.append({"path": item.path, "error": str(exc)})
            continue
        observations[item.key] = {"path": item.path, "real_path": item.real_path, "stat": st}
        candidates = set(by_inode[st["device_id"], st["inode"]])
        loc = locations.get(item.real_path)
        if loc:
            prior = by_asset[loc["asset_id"]]
            if not prior or any((r["device_id"], r["inode"]) ==
                                (st["device_id"], st["inode"]) for r in prior):
                candidates.add(loc["asset_id"])
        if len(candidates) == 1 and item.key in legacy and legacy[item.key] not in candidates:
            legacy_mismatches.append({"media_key": item.key, "path": item.path,
                                      "bound_asset": legacy[item.key], "current_asset": next(iter(candidates))})
        if len(candidates) > 1:
            conflicts.append({"title": item.title, "path": item.path,
                              "target": item.real_path, "assets": sorted(candidates)})
    return {"items": len(items), "shows": len(shows), "conflict_count": len(conflicts),
            "conflicts": conflicts, "stat_errors": errors, "observations": observations,
            "legacy_binding_mismatches": legacy_mismatches}


def current_states(db, p):
    return rows(db, PREFIX + "asset_playback_state", "WHERE asset_id IN (?,?)",
                (p["source_asset"], p["target_asset"]))


def equal_rows(actual, expected):
    return sorted(map(digest, actual)) == sorted(map(digest, expected))


def observation_status(db, p, suffix, evidence_key, id_key, selector):
    expected = p[evidence_key]
    originals = [one(db, PREFIX + suffix, f"WHERE {id_key}=?", (r[id_key],)) for r in expected]
    active_source = rows(db, PREFIX + suffix, "WHERE asset_id=? AND valid_to IS NULL",
                         (p["source_asset"],))
    if equal_rows(active_source, expected) and originals == expected:
        return PairStatus.READY
    require(not active_source, f"{suffix}: active source evidence changed")
    for old, original in zip(expected, originals):
        require(original is not None and original["valid_to"] is not None,
                f"{suffix}: missing original version")
        retained = {k: v for k, v in original.items() if k not in ("valid_to", "last_seen")}
        require(retained == {k: v for k, v in old.items() if k not in ("valid_to", "last_seen")},
                f"{suffix}: original history changed")
        fields = selector.split(",")
        where = " AND ".join(f"{key}=?" for key in fields)
        current = one(db, PREFIX + suffix, f"WHERE {where} AND valid_to IS NULL",
                      tuple(old[k] for k in fields))
        require(current is not None and current["asset_id"] == p["target_asset"],
                f"{suffix}: no canonical replacement")
    return PairStatus.DONE


def validate_playback(db, p, status):
    states = {s["asset_id"]: s for s in current_states(db, p)}
    expected = {s["asset_id"]: s for s in p["playback_states"]}
    source, target = (expected.get(p[k]) for k in ("source_asset", "target_asset"))
    newer = bool(source and (not target or source["updated_at"] > target["updated_at"]))
    require(newer == p["transfer_newer_playhead"], "plan transfer decision contradicts timestamps")
    require(states.get(p["source_asset"]) == source, "source playback changed")
    if status == PairStatus.READY or not newer:
        require(states == expected, "playback evidence changed")
        return
    event_key = f"playhead-transfer:{p['source_asset']}:{source.get('last_event_id') or source['updated_at']}"
    event = one(db, PREFIX + "playback_events", "WHERE asset_id=? AND event_key=?",
                (p["target_asset"], event_key))
    require(event and event["event_type"] == "playhead_transferred" and
            json.loads(event["payload_json"]) == {"source_asset_id": p["source_asset"], "reason": REASON},
            "missing prior repair transfer")
    current = states.get(p["target_asset"])
    require(current and current["last_event_id"] == event["event_id"], "canonical playback changed after repair")
    require(all(current[k] == source[k] for k in ("position", "duration", "completed")),
            "canonical playhead differs from transferred source")


def validate_pair(db, library, p):
    require(stat_value(p["target_path"]) == p["expected_stat"], "target stat changed")
    actual_assets = rows(db, PREFIX + "assets", "WHERE asset_id IN (?,?)",
                         (p["source_asset"], p["target_asset"]))
    require(equal_rows(actual_assets, p["expected_assets"]), "asset evidence changed")
    require(len(actual_assets) == 2 and p["work_id"] is not None and
            all(a["work_id"] == p["work_id"] for a in actual_assets), "work equality failed")
    identities = rows(db, PREFIX + "file_identities", "WHERE asset_id IN (?,?)",
                      (p["source_asset"], p["target_asset"]))
    require(equal_rows(identities, p["expected_identities"]), "identity evidence changed")
    physical = one(db, PREFIX + "file_identities", "WHERE identity_id=?", (p["identity_id"],))
    require(physical and physical["asset_id"] == p["target_asset"] and
            all(physical[k] == p["expected_stat"][k] for k in ("device_id", "inode", "size", "mtime_ns")),
            "canonical physical identity mismatch")
    require(not any(r["device_id"] is not None or r["inode"] is not None
                    for r in identities if r["asset_id"] == p["source_asset"]),
            "source is not path-only")
    owners = rows(db, PREFIX + "file_identities", "WHERE device_id=? AND inode=?",
                  (physical["device_id"], physical["inode"]))
    require({r["asset_id"] for r in owners} == {p["target_asset"]}, "inode ownership changed")
    require(rows(db, PREFIX + "locations", "WHERE asset_id=? AND path=?",
                 (p["target_asset"], p["target_path"])), "no target historical path")
    for loc in p["locations_to_version"]:
        require(os.path.realpath(loc["path"]) == p["target_path"] and
                stat_value(loc["path"]) == p["expected_stat"], "source path changed")
    selected = library.item_for_path(p["target_path"])
    require(selected and selected.real_path == p["target_path"], "library selects another encode")
    for alias in p["aliases_to_version"]:
        if alias["namespace"] == "parser-key":
            require(library.items_by_key.get(alias["alias"]) is selected, "parser alias selects another item")
        elif alias["namespace"] == "library-relative-path":
            require(library.item_for_rel(alias["alias"]) is selected, "relative alias selects another item")
        else:
            raise ChangedEvidence("unreviewed alias namespace")
    location_status = observation_status(db, p, "locations", "locations_to_version", "location_id", "path")
    alias_status = observation_status(db, p, "aliases", "aliases_to_version", "alias_id", "namespace,alias")
    require(location_status == alias_status, "partially versioned pair")
    validate_playback(db, p, location_status)
    if location_status == PairStatus.READY:
        validate_legacy_evidence(db, p)
    return location_status


def validate_legacy_evidence(db, p):
    for old in p["legacy_bindings_to_review"]:
        require(one(db, PREFIX + "legacy_keys", "WHERE source=? AND media_key=?",
                    (old["source"], old["media_key"])) == old, "legacy binding evidence changed")
        require(equal_rows(rows(db, "progress", "WHERE media_key=?", (old["media_key"],)),
                           p["expected_progress"][old["media_key"]]), "v1 playback evidence changed")
        asset_id = old["asset_id"]
        require(one(db, PREFIX + "assets", "WHERE asset_id=?", (asset_id,)) ==
                p["expected_legacy_assets"][asset_id], "legacy asset evidence changed")
        for table, key in (("file_identities", "expected_legacy_identities"),
                           ("locations", "expected_legacy_locations")):
            if asset_id in p[key]:
                require(equal_rows(rows(db, PREFIX + table, "WHERE asset_id=?", (asset_id,)),
                                   p[key][asset_id]), f"legacy {table} evidence changed")


def snapshot(db):
    result = {}
    for table in rows(db, "sqlite_master", "WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        name = table["name"]
        columns = list(db.execute(f'PRAGMA table_info("{name}")'))
        keys = [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]]
        if not keys:
            raise RuntimeError(f"table without primary key: {name}")
        result[name] = {tuple(row[k] for k in keys): digest(row) for row in rows(db, name)}
    return result


def authorize(changes, table, key):
    changes["updated"][table].add(tuple(key))


def project_progress(catalog, item, asset_id, changes):
    # Reuse the service's projection calculation, without constructing a service
    # (which would recover sessions, reconcile imports and start scanning).
    context = SimpleNamespace(catalog=catalog, clock=catalog.clock)
    progress = LegacyProgressMixin._legacy_progress_for_asset(context, asset_id)
    if progress is None:
        return False
    db = catalog.connection
    for table in ("progress", PREFIX + "v1_shadow"):
        if not one(db, table, "WHERE media_key=?", (item.key,)):
            changes["inserted"][table] += 1
        else:
            authorize(changes, table, (item.key,))
    catalog.project_v1_progress(item.key, **progress, title=item.title,
                                rel_path=item.rel_path, asset_id=asset_id, connection=db)
    return True


def legacy_same_file(db, p, asset_id):
    if asset_id in (p["source_asset"], p["target_asset"]):
        return True
    expected_ids = p["expected_legacy_identities"].get(asset_id, [])
    expected_locs = p["expected_legacy_locations"].get(asset_id, [])
    physical = [r for r in expected_ids if r["inode"] is not None]
    # An older mount's device number may differ. Work, historical exact path,
    # inode, byte size and nanosecond mtime must all agree; titles never suffice.
    return bool(physical and
                all(all(r[k] == p["expected_stat"][k] for k in ("inode", "size", "mtime_ns"))
                    for r in physical) and
                any(r["path"] == p["target_path"] for r in expected_locs) and
                equal_rows(rows(db, PREFIX + "file_identities", "WHERE asset_id=?", (asset_id,)), expected_ids) and
                equal_rows(rows(db, PREFIX + "locations", "WHERE asset_id=?", (asset_id,)), expected_locs))


def repair_legacy(catalog, library, p, changes):
    report = []
    db = catalog.connection
    for old in p["legacy_bindings_to_review"]:
        record = {"media_key": old["media_key"], "before_asset": old["asset_id"],
                  "target_asset": p["target_asset"]}
        current = one(db, PREFIX + "legacy_keys", "WHERE source=? AND media_key=?",
                      (old["source"], old["media_key"]))
        item = library.items_by_key.get(old["media_key"])
        bound = catalog.lookup_asset(old["asset_id"])
        if current != old:
            record["skipped"] = "binding changed"
        elif old["source"] != "v1-progress" or not item or item.real_path != p["target_path"]:
            record["skipped"] = "key no longer selects this item"
        elif bound != p["expected_legacy_assets"][old["asset_id"]] or bound["work_id"] != p["work_id"]:
            record["skipped"] = "bound asset/work changed"
        elif not legacy_same_file(db, p, old["asset_id"]):
            record["skipped"] = "unrelated exact encode retained"
        elif not equal_rows(rows(db, "progress", "WHERE media_key=?", (old["media_key"],)),
                            p["expected_progress"][old["media_key"]]):
            record["skipped"] = "v1 progress evidence changed"
        else:
            record["rebound"] = old["asset_id"] != p["target_asset"]
            if record["rebound"]:
                authorize(changes, PREFIX + "legacy_keys", (old["source"], old["media_key"]))
                catalog.bind_legacy_key(p["target_asset"], old["media_key"], source=old["source"],
                                        replace=True, metadata=json.loads(old["metadata_json"] or "null"), connection=db)
            record["projected"] = project_progress(catalog, item, p["target_asset"], changes)
        report.append(record)
    return report


def repair_pair(catalog, library, p, changes):
    db = catalog.connection
    result = {"title": p["title"], "source_asset": p["source_asset"], "target_asset": p["target_asset"]}
    try:
        status = validate_pair(db, library, p)
    except (ChangedEvidence, OSError) as exc:
        return {**result, "status": PairStatus.SKIPPED, "reason": str(exc)}
    result["status"] = status
    if status == PairStatus.DONE:
        return result
    if p["transfer_newer_playhead"]:
        source = catalog.get_asset_state(p["source_asset"])
        before = catalog.get_asset_state(p["target_asset"])
        if not catalog.transfer_playhead(p["source_asset"], p["target_asset"], reason=REASON, connection=db):
            raise RuntimeError("approved transfer unexpectedly declined")
        changes["inserted"][PREFIX + "playback_events"] += 1
        if before is None:
            changes["inserted"][PREFIX + "asset_playback_state"] += 1
        else:
            authorize(changes, PREFIX + "asset_playback_state", (p["target_asset"],))
        after = catalog.get_asset_state(p["target_asset"])
        if any(after[k] != source[k] for k in ("position", "duration", "completed")):
            raise RuntimeError("incorrect transferred playhead")
        result["playhead"] = {"before": before, "after": after, "source_retained": source}
    for old in p["locations_to_version"]:
        authorize(changes, PREFIX + "locations", (old["location_id"],))
        catalog.record_location(p["target_asset"], old["path"], location_kind=old["location_kind"],
                                source=old["source"], metadata=json.loads(old["metadata_json"] or "null"), connection=db)
        changes["inserted"][PREFIX + "locations"] += 1
    for old in p["aliases_to_version"]:
        authorize(changes, PREFIX + "aliases", (old["alias_id"],))
        catalog.record_alias(p["target_asset"], old["alias"], namespace=old["namespace"],
                             provenance=old["provenance"], metadata=json.loads(old["metadata_json"] or "null"), connection=db)
        changes["inserted"][PREFIX + "aliases"] += 1
    result["legacy"] = repair_legacy(catalog, library, p, changes)
    return result


def invalidate_reused_inode(db, exception, changes):
    old = exception["inode_identities"][0]
    result = {"identity_before": old, "playback_transferred": False}
    try:
        current = one(db, PREFIX + "file_identities", "WHERE identity_id=?", (old["identity_id"],))
        nulled = {**old, "device_id": None, "inode": None}
        require(current in (old, nulled), "reused inode identity changed")
        path = exception["location"]["path"]
        require(stat_value(path) == exception["stat"], "Silo stat changed")
        require(old["size"] != exception["stat"]["size"] and
                old["mtime_ns"] != exception["stat"]["mtime_ns"], "inode reuse proof missing")
        for loc in exception["asset_locations"]:
            require(os.path.realpath(loc["path"]) == path and stat_value(loc["path"]) == exception["stat"],
                    "Silo path changed")
        for asset in exception["assets"]:
            actual = one(db, PREFIX + "assets", "WHERE asset_id=?", (asset["asset_id"],))
            require(actual == {k: v for k, v in asset.items() if k != "title"}, "exception asset changed")
        require(len({a["work_id"] for a in exception["assets"]}) == 2, "exception works not distinct")
        require(equal_rows(rows(db, PREFIX + "file_identities", "WHERE asset_id=?",
                                (exception["location"]["asset_id"],)), exception["path_identities"]),
                "Silo identity evidence changed")
        require(equal_rows(rows(db, PREFIX + "locations", "WHERE asset_id=? AND valid_to IS NULL",
                                (exception["location"]["asset_id"],)), exception["asset_locations"]),
                "Silo active locations changed")
        require(equal_rows(rows(db, PREFIX + "locations", "WHERE asset_id=?", (old["asset_id"],)),
                           exception["retired_asset_locations"]), "Barbarians locations changed")
        for loc in exception["retired_asset_locations"]:
            try:
                os.stat(loc["path"])
            except FileNotFoundError:
                continue
            raise ChangedEvidence("Barbarians path still exists")
        require(equal_rows(rows(db, PREFIX + "asset_playback_state", "WHERE asset_id IN (?,?)",
                                tuple(a["asset_id"] for a in exception["assets"])), exception["playback_states"]),
                "exception playback changed")
        owners = rows(db, PREFIX + "file_identities", "WHERE device_id=? AND inode=?",
                      (old["device_id"], old["inode"]))
        require(owners == ([old] if current == old else []), "exception inode owners changed")
    except (ChangedEvidence, OSError) as exc:
        return {**result, "status": PairStatus.SKIPPED, "reason": str(exc)}
    if current == nulled:
        return {**result, "status": PairStatus.DONE}
    # No catalog invalidation API exists. Keep the row and audit its full prior
    # contents; unlike a merge, this only removes disproven physical ownership.
    updated = db.execute("""UPDATE video_v2_file_identities SET device_id=NULL, inode=NULL
        WHERE identity_id=? AND asset_id=? AND device_id=? AND inode=? AND size=? AND mtime_ns=?""",
        tuple(old[k] for k in ("identity_id", "asset_id", "device_id", "inode", "size", "mtime_ns")))
    if updated.rowcount != 1:
        raise RuntimeError("guarded inode invalidation did not update exactly one row")
    authorize(changes, PREFIX + "file_identities", (old["identity_id"],))
    return {**result, "status": PairStatus.READY, "identity_after": nulled}


def postcheck(db, before, changes, report, library, expected_items):
    integrity = [r[0] for r in db.execute("PRAGMA integrity_check")]
    foreign_keys = [list(r) for r in db.execute("PRAGMA foreign_key_check")]
    report["integrity_check"], report["foreign_key_check"] = integrity, foreign_keys
    if integrity != ["ok"] or foreign_keys:
        raise RuntimeError("integrity/foreign-key check failed")
    after = snapshot(db)
    report["row_counts"] = {}
    if before.keys() != after.keys():
        raise RuntimeError("schema tables changed")
    for table, old in before.items():
        new = after[table]
        removed = old.keys() - new.keys()
        inserted = new.keys() - old.keys()
        modified = {key for key in old.keys() & new.keys() if old[key] != new[key]}
        report["row_counts"][table] = dict(before=len(old), after=len(new), delta=len(new)-len(old),
                                           modified=len(modified), deleted=len(removed))
        if removed or len(inserted) != changes["inserted"][table] or modified - changes["updated"][table]:
            raise RuntimeError(f"unexpected original row mutation or row growth: {table}")
    for pair in report["pairs"]:
        if "playhead" not in pair:
            continue
        for field, asset in (("source_retained", "source_asset"), ("after", "target_asset")):
            actual = one(db, PREFIX + "asset_playback_state", "WHERE asset_id=?", (pair[asset],))
            if actual != pair["playhead"][field]:
                raise RuntimeError("post-check playhead retention failed")
    report["scan_after"] = scan(db, library)
    after_scan, before_scan = report["scan_after"], report["scan_before"]
    if after_scan["items"] != expected_items or after_scan["observations"] != before_scan["observations"]:
        raise RuntimeError("library membership/stat evidence changed during repair")
    if after_scan["conflicts"] or after_scan["stat_errors"]:
        raise RuntimeError("post-repair file-observation conflicts/stat errors remain")


def repair_timestamp(db):
    values = [time.time()]
    for table, columns in (("locations", ("valid_from", "last_seen")),
                           ("aliases", ("valid_from", "last_seen")),
                           ("asset_playback_state", ("updated_at",)),
                           ("work_watch_state", ("updated_at",))):
        for column in columns:
            values.append(db.execute(f"SELECT MAX({column}) FROM {PREFIX}{table}").fetchone()[0] or 0)
    return max(values)


def run_repair(db, plan, report, library, apply):
    db.row_factory = sqlite3.Row
    versions = {r[0] for r in db.execute("SELECT version FROM video_v2_schema_migrations")}
    if versions != set(range(1, SCHEMA_VERSION + 1)):
        raise RuntimeError("schema must already match deployed catalog; migration refused")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("BEGIN IMMEDIATE")
    try:
        timestamp = repair_timestamp(db)
        report["repair_timestamp"] = timestamp
        catalog = MediaAssetCatalog(connection=db, clock=lambda: timestamp)
        before = snapshot(db)
        changes = {"inserted": Counter(), "updated": defaultdict(set)}
        report["scan_before"] = scan(db, library)
        if report["scan_before"]["items"] != plan["expected_library_items"] or report["scan_before"]["stat_errors"]:
            raise RuntimeError("incomplete read-only library scan")
        report["pairs"] = []
        for p in plan["same_file_pairs"]:
            report["pairs"].append(repair_pair(catalog, library, p, changes))
        report["inode_exceptions"] = [invalidate_reused_inode(db, e, changes)
                                       for e in plan["reused_inode_exceptions"]]
        postcheck(db, before, changes, report, library, plan["expected_library_items"])
        report["sqlite_changes"] = db.total_changes
        if apply:
            db.commit()
            report["committed"] = True
        else:
            db.rollback()
            report["rolled_back"] = True
    except BaseException:
        db.rollback()
        report["rolled_back"] = True
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default; repair memory snapshot and roll back")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--i-have-stopped-video-library", action="store_true")
    args = parser.parse_args(argv)
    args.db = args.db.resolve(strict=True)
    args.plan = args.plan.resolve(strict=True)
    if args.apply and not args.i_have_stopped_video_library:
        parser.error("--apply requires --i-have-stopped-video-library")
    if args.report.resolve() in (args.db, args.plan):
        parser.error("report must not overwrite the database or plan")
    os.umask(0o077)
    # Exclusive creation both preserves earlier audits and checks report access
    # before the transaction. A report-writing failure must never imply rollback.
    with args.report.open("x") as output:
        started = time.monotonic()
        report = dict(db=str(args.db), plan=str(args.plan), apply=args.apply,
                      committed=False, started_at=time.time(), catalog_module=__import__(
                          MediaAssetCatalog.__module__, fromlist=["__file__"]).__file__)
        code = 1
        try:
            plan = load_plan(args.plan)
            report["plan_sha256"] = hashlib.sha256(args.plan.read_bytes()).hexdigest()
            if args.apply:
                report["quiescence"] = ensure_unused(args.db)
            mode = "rw" if args.apply else "ro"
            with closing(sqlite3.connect(args.db.as_uri() + f"?mode={mode}", uri=True, timeout=5)) as source:
                if args.apply:
                    run_repair(source, plan, report, MediaLibrary(default_sources()), True)
                else:
                    with closing(sqlite3.connect(":memory:")) as memory:
                        source.backup(memory)
                        run_repair(memory, plan, report, MediaLibrary(default_sources()), False)
            code = 0
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            report["runtime_seconds"] = round(time.monotonic() - started, 3)
            report["pair_counts"] = dict(Counter(p["status"] for p in report.get("pairs", [])))
            approved = report["pair_counts"].get(PairStatus.READY, 0)
            report["applied_pairs"] = approved if report["committed"] else 0
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps({k: report[k] for k in ("committed", "pair_counts", "applied_pairs", "runtime_seconds")}))
        if report.get("error"):
            print(report["error"])
        return code


if __name__ == "__main__":
    raise SystemExit(main())
