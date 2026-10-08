"""Rebind reviewed device-churn keys without merging or deleting assets/history."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from types import SimpleNamespace

from pi.apps.video_library.legacy_progress import LegacyProgressMixin
from pi.apps.video_library.maintenance import legacy_churn_evidence as evidence
from pi.apps.video_library.maintenance.same_file_repair import (
    ChangedEvidence, PairStatus, one, project_progress, require,
)

PREFIX = evidence.PREFIX


def _track_row(changes, table, key, before, after):
    require(after is not None, f"unexpected missing repair row: {table}")
    if before != after:
        changes["inserted" if before is None else "updated"][table].add(tuple(key))


def _transfer(catalog, pair, changes):
    if not pair["transfer"]:
        return None
    db = catalog.connection
    require(catalog.transfer_playhead(pair["source_asset"], pair["target_asset"],
                                     reason=evidence.REASON, connection=db),
            "approved legacy playhead transfer declined")
    after = catalog.get_asset_state(pair["target_asset"])
    _track_row(changes, PREFIX + "asset_playback_state", (pair["target_asset"],),
               pair["target_state"], after)
    changes["inserted"][PREFIX + "playback_events"].add((after["last_event_id"],))
    return {"title": pair["title"], "media_key": pair["media_key"],
            "before": pair["target_state"], "after": after, "source": pair["source_state"]}


def _project(catalog, pair, item, changes):
    # The existing one-off repair owns the v1/shadow projection calculation.
    temporary = {"inserted": Counter(), "updated": defaultdict(set)}
    if not project_progress(catalog, item, pair["target_asset"], temporary):
        return False
    for table, field in (("progress", "progress"), (PREFIX + "v1_shadow", "shadow")):
        after = one(catalog.connection, table, "WHERE media_key=?", (pair["media_key"],))
        _track_row(changes, table, (pair["media_key"],), pair[field], after)
    return True


def _verify_transferred_state(catalog, pair, current):
    source, before, after = pair["source_state"], pair["target_state"], current["target_state"]
    require(after is not None, "transferred legacy playhead disappeared")
    event_key = f"playhead-transfer:{pair['source_asset']}:{source.get('last_event_id') or source['updated_at']}"
    event = one(catalog.connection, PREFIX + "playback_events",
                "WHERE asset_id=? AND event_key=?", (pair["target_asset"], event_key))
    require(event is not None and event["event_type"] == "playhead_transferred" and
            json.loads(event["payload_json"]) == {
                "source_asset_id": pair["source_asset"], "reason": evidence.REASON},
            "missing reviewed legacy transfer event")
    require(after["last_event_id"] == event["event_id"] and
            after["updated_at"] == event["observed_at"] and
            after["updated_at"] >= source["updated_at"], "transferred legacy event/state changed")
    require(all(after[key] == source[key] == event[key] for key in evidence.PLAYHEAD_FIELDS),
            "transferred legacy playhead changed")
    require(after["play_count"] == max(source["play_count"], (before or {}).get("play_count", 0))
            and after["last_session_id"] == (before or {}).get("last_session_id"),
            "transferred legacy session/count changed")


def _verify_projection(catalog, pair, current):
    context = SimpleNamespace(catalog=catalog, clock=catalog.clock)
    progress = LegacyProgressMixin._legacy_progress_for_asset(context, pair["target_asset"])
    if progress is None:
        require(current["progress"] == pair["progress"] and current["shadow"] == pair["shadow"],
                "unprojected legacy progress changed")
        return
    expected = {"media_key": pair["media_key"], **progress,
                "title": pair["title"], "rel_path": pair["rel_path"]}
    require(current["progress"] == expected, "canonical v1 projection changed")
    shadow = current["shadow"]
    require(shadow is not None and shadow["asset_id"] == pair["target_asset"] and
            shadow["was_present"] == 1 and shadow["source_updated"] == progress["updated"] and
            shadow["row_digest"] == catalog._v1_row_digest(expected) and
            shadow["covered_state_digest"] == catalog._v1_covered_state_digest(
                catalog.connection, pair["target_asset"]), "canonical v1 shadow changed")
    require(json.loads(shadow["raw_json"]) == expected, "canonical v1 shadow payload changed")


def verify_done(catalog, pair, item, identities):
    done, current = evidence.validate_pair(catalog.connection, pair, item, catalog, identities)
    require(done, "legacy binding still points to the old asset")
    if pair["transfer"]:
        _verify_transferred_state(catalog, pair, current)
    else:
        require(current["target_state"] == pair["target_state"], "retained canonical playhead changed")
    _verify_projection(catalog, pair, current)


def repair_pair(catalog, pair, item, changes, identities):
    result = {"media_key": pair["media_key"], "kind": pair["kind"],
              "source_asset": pair["source_asset"], "target_asset": pair["target_asset"]}
    try:
        done, _ = evidence.validate_pair(catalog.connection, pair, item, catalog, identities)
        if done:
            verify_done(catalog, pair, item, identities)
            return {**result, "status": PairStatus.DONE}
        transfer = _transfer(catalog, pair, changes)
        old = pair["legacy"]
        catalog.bind_legacy_key(pair["target_asset"], pair["media_key"], source=old["source"],
                                replace=True, metadata=json.loads(old["metadata_json"] or "null"),
                                connection=catalog.connection)
        changes["updated"][PREFIX + "legacy_keys"].add((old["source"], pair["media_key"]))
        projected = _project(catalog, pair, item, changes)
        verify_done(catalog, pair, item, identities)
        return {**result, "status": PairStatus.READY, "transfer": transfer, "projected": projected}
    except (ChangedEvidence, OSError) as exc:
        return {**result, "status": PairStatus.SKIPPED, "reason": str(exc)}


def run_legacy_repair(catalog, plan, prior, library, changes, report):
    evidence.validate_churn_plan(plan, prior)
    identities = evidence.IdentityIndex.read(catalog.connection)
    items, _ = library.snapshot()
    selected = {item.key: item for item in items}
    results = [repair_pair(catalog, pair, selected.get(pair["media_key"]), changes, identities)
               for pair in plan["pairs"]]
    report["legacy_repairs"] = results
    report["legacy_counts"] = dict(Counter(row["status"] for row in results))
    report["legacy_proof_counts"] = dict(Counter(row["kind"] for row in results))
    report["playhead_transfers"] = [row["transfer"] for row in results if row.get("transfer")]
    report["legacy_projected"] = sum(bool(row.get("projected")) for row in results)
    require(not any(row["status"] == PairStatus.SKIPPED for row in results),
            "changed legacy evidence skipped; entire repair rolled back")


def verify_legacy_repair(catalog, plan, library):
    identities = evidence.IdentityIndex.read(catalog.connection)
    items, _ = library.snapshot()
    selected = {item.key: item for item in items}
    for pair in plan["pairs"]:
        verify_done(catalog, pair, selected.get(pair["media_key"]), identities)
