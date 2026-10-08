"""Pinned evidence for the one-time Silo filesystem-identity repair."""
from __future__ import annotations

from collections import Counter
from enum import Enum
import os
import sqlite3
from typing import Any

from pi.apps.video_library.filesystem_identity import filesystem_device_id
from pi.apps.video_library.maintenance.same_file_repair import (
    ChangedEvidence,
    equal_rows,
    one,
    require,
    rows,
)

class RepairPhase(str, Enum):
    READY = "ready"
    DONE = "done"


PREFIX = "video_v2_"
PLAN_VERSION = 1
WRONG_ASSET = "ast_7d8867cca92542dabab4cb845e6b5113"
CORRECT_ASSET = "ast_31b4bf73c8324ee98b32d2aa81d81010"
AUDIO_ASSET = "ast_d6006b1770f5472aa498fd87199ad08d"
SILO_WORK = "wrk_4226b612c5704c7382378acd3bf6ce17"
BARBARIANS_WORK = "wrk_ca7aec518e714fac823e0c59999571c1"
TARGET_PATH = (
    "/mnt/movingparts/torrent/New/Silo.S03E07.1080p.WEB.H264-CAKES/"
    "silo.s03e07.1080p.web.h264-cakes.mkv"
)
LINK_PATH = "/mnt/movingparts/links/New/silo_s03e07"
AUDIO_TARGET_PATH = (
    "/mnt/movingparts/torrent/New/Silo S03E07 Radio with Audio Description 1080p "
    "ATVP WEB-DL DDP5 1 Atmos H 264-Kitsune/Silo S03E07 Radio with Audio Description "
    "1080p ATVP WEB-DL DDP5 1 Atmos H 264-Kitsune.mkv"
)
AUDIO_LINK_PATH = "/mnt/movingparts/links/New/Silo_S03E07_Radio_with_Audio_Description_ATVP"
MEDIA_KEY = "episode:tv:silo:s3:e7"
FILESYSTEM_DEVICE_ID = "fsuuid:23652e98-ced8-456a-8174-50aa9c86f889"
LEGACY_DEVICE_ID = "2113"
TARGET_INODE = 151003051
TARGET_SIZE = 4431739775
TARGET_MTIME_NS = 1787217266069220939
WRONG_IDENTITY = "fid_22fbbd3d48cb4fa18f313214e0e6de19"
OLD_BARBARIANS_IDENTITY = "fid_739229471d36430d9427c1ded7408cee"
EXPECTED_AUDIO_HISTORY_COUNTS = {"sessions": 3, "events": 78}
EXPECTED_COUNTERPART_COUNTS = {"duplicate_works": 10, "assets": 20}

EXPECTED_ASSETS = [
    {"asset_id": WRONG_ASSET, "work_id": BARBARIANS_WORK, "asset_kind": "provisional-file",
     "expected_size": 1409266220, "fingerprint_algorithm": None, "fingerprint": None,
     "metadata_json": '{"created_from":"library","source":"movingparts"}',
     "created_at": 1787000864.0862074, "updated_at": 1787000864.0917265},
    {"asset_id": AUDIO_ASSET, "work_id": SILO_WORK, "asset_kind": "provisional-file",
     "expected_size": 4431549991, "fingerprint_algorithm": None, "fingerprint": None,
     "metadata_json": '{"created_from":"library","source":"movingparts"}',
     "created_at": 1787211354.5167782, "updated_at": 1787211354.5341048},
    {"asset_id": CORRECT_ASSET, "work_id": SILO_WORK, "asset_kind": "provisional-file",
     "expected_size": None, "fingerprint_algorithm": None, "fingerprint": None,
     "metadata_json": '{"created_from":"library","source":"movingparts"}',
     "created_at": 1787853765.5121295, "updated_at": 1787853765.525173},
]
EXPECTED_WORKS = [
    {"work_id": BARBARIANS_WORK, "kind": "episode", "title": "A New Reik", "year": None,
     "series": "Barbarians 2020", "season": 1, "episode": 4, "external_ids_json": None,
     "metadata_json": '{"created_from":"library"}', "created_at": 1786435417.494408,
     "updated_at": 1786435417.494408},
    {"work_id": SILO_WORK, "kind": "episode", "title": "Radio with Audio Description ATVP",
     "year": None, "series": "Silo", "season": 3, "episode": 7,
     "external_ids_json": None, "metadata_json": '{"created_from":"library"}',
     "created_at": 1787211354.5320318, "updated_at": 1787211354.5320318},
]
EXPECTED_FIXED_IDENTITIES = {
    OLD_BARBARIANS_IDENTITY: {"identity_id": OLD_BARBARIANS_IDENTITY, "asset_id": WRONG_ASSET,
        "device_id": "2113", "inode": TARGET_INODE, "size": 1409266220,
        "mtime_ns": 1703033633739932272, "fingerprint_algorithm": None,
        "fingerprint": None, "observed_at": 1787000864.0862074},
    WRONG_IDENTITY: {"identity_id": WRONG_IDENTITY, "asset_id": WRONG_ASSET,
        "device_id": "2113", "inode": TARGET_INODE, "size": TARGET_SIZE,
        "mtime_ns": TARGET_MTIME_NS, "fingerprint_algorithm": None,
        "fingerprint": None, "observed_at": 1791405626.0678825},
    "fid_d44e4d95ecc74e78b2329e5f04dd7b22": {
        "identity_id": "fid_d44e4d95ecc74e78b2329e5f04dd7b22", "asset_id": CORRECT_ASSET,
        "device_id": None, "inode": None, "size": None, "mtime_ns": None,
        "fingerprint_algorithm": None, "fingerprint": None, "observed_at": 1787853765.5121295},
    "fid_d0413bbb3d57433785572b0f7decc70f": {
        "identity_id": "fid_d0413bbb3d57433785572b0f7decc70f", "asset_id": CORRECT_ASSET,
        "device_id": "2161", "inode": TARGET_INODE, "size": TARGET_SIZE,
        "mtime_ns": TARGET_MTIME_NS, "fingerprint_algorithm": None,
        "fingerprint": None, "observed_at": 1791348709.1829915},
    "fid_64abb63f66a0421abe10b4811a6a3cf6": {
        "identity_id": "fid_64abb63f66a0421abe10b4811a6a3cf6", "asset_id": AUDIO_ASSET,
        "device_id": "2081", "inode": 151002689, "size": 4431549991,
        "mtime_ns": 1787211219877312406, "fingerprint_algorithm": None,
        "fingerprint": None, "observed_at": 1787211354.5167782},
}
EXPECTED_SOURCE_LOCATIONS = [
    {"location_id": "loc_eef2c3ffd2d5458cae94b7161eac1faa", "asset_id": WRONG_ASSET,
     "path": TARGET_PATH, "location_kind": "library-target", "source": "movingparts",
     "valid_from": 1791405626.0678825, "valid_to": None, "last_seen": 1791440046.8442137,
     "metadata_json": None},
    {"location_id": "loc_35a0f09e9ee4444f83817ca2964a90d6", "asset_id": WRONG_ASSET,
     "path": LINK_PATH, "location_kind": "library-link", "source": "movingparts",
     "valid_from": 1791405626.0757353, "valid_to": None, "last_seen": 1791440046.844424,
     "metadata_json": None},
]
EXPECTED_PRIOR_LOCATIONS = [
    {"location_id": "loc_2fc8014ede1f44b585ba135c3311833e", "asset_id": CORRECT_ASSET,
     "path": TARGET_PATH, "location_kind": "library-target", "source": "movingparts",
     "valid_from": 1787853765.5121295, "valid_to": 1791405626.0678825,
     "last_seen": 1791405626.0678825, "metadata_json": None},
    {"location_id": "loc_c337f25f7f114376b4ab49a51c5d5fe7", "asset_id": CORRECT_ASSET,
     "path": LINK_PATH, "location_kind": "library-link", "source": "movingparts",
     "valid_from": 1787853765.5270493, "valid_to": 1791405626.0757353,
     "last_seen": 1791405626.0757353, "metadata_json": None},
]
EXPECTED_SOURCE_ALIASES = [
    {"alias_id": "als_4fe7fa81e08d4af1ac79655b4ffbfa1f", "asset_id": WRONG_ASSET,
     "namespace": "library-relative-path", "alias": "/New/Silo_S03E07_Radio_with_Audio_Description_ATVP",
     "provenance": "movingparts", "valid_from": 1791405626.0759943, "valid_to": None,
     "last_seen": 1791434579.103231, "metadata_json": None},
    {"alias_id": "als_b15297b34b7c47698aafecaa98749bc9", "asset_id": WRONG_ASSET,
     "namespace": "library-relative-path", "alias": "/New/silo_s03e07",
     "provenance": "movingparts", "valid_from": 1791405626.0767248, "valid_to": None,
     "last_seen": 1791440046.8446271, "metadata_json": None},
    {"alias_id": "als_88b4b180f12b49d88f32987c7fb16730", "asset_id": WRONG_ASSET,
     "namespace": "parser-key", "alias": MEDIA_KEY, "provenance": "movingparts",
     "valid_from": 1791405626.0769608, "valid_to": None,
     "last_seen": 1791440046.8448353, "metadata_json": None},
]
EXPECTED_PRIOR_ALIASES = [
    {"alias_id": "als_35fb7ea88afc4058bf4f8dc54308a629", "asset_id": CORRECT_ASSET,
     "namespace": "library-relative-path", "alias": "/New/Silo_S03E07_Radio_with_Audio_Description_ATVP",
     "provenance": "movingparts", "valid_from": 1787853765.5273113,
     "valid_to": 1791405626.0759943, "last_seen": 1791405626.0759943, "metadata_json": None},
    {"alias_id": "als_951d22b1157c404a8e88256c91c89aca", "asset_id": CORRECT_ASSET,
     "namespace": "library-relative-path", "alias": "/New/silo_s03e07",
     "provenance": "movingparts", "valid_from": 1787853765.527635,
     "valid_to": 1791405626.0767248, "last_seen": 1791405626.0767248, "metadata_json": None},
    {"alias_id": "als_1e096e7bca3e4b6787ff5e10a406843d", "asset_id": CORRECT_ASSET,
     "namespace": "parser-key", "alias": MEDIA_KEY, "provenance": "movingparts",
     "valid_from": 1787853765.5278845, "valid_to": 1791405626.0769608,
     "last_seen": 1791405626.0769608, "metadata_json": None},
]
EXPECTED_LEGACY = {"source": "v1-progress", "media_key": MEDIA_KEY, "asset_id": AUDIO_ASSET,
    "first_seen": 1787211354.5344992, "last_seen": 1787216563.8496957,
    "metadata_json": '{"rel_path":"/New/Silo_S03E07_Radio_with_Audio_Description_ATVP",'
                     '"title":"Radio with Audio Description ATVP"}'}
EXPECTED_AUDIO_STATE = {"asset_id": AUDIO_ASSET, "position": 0.0, "duration": 3278.176,
    "completed": 0, "play_count": 3, "updated_at": 1787216162.537501,
    "last_session_id": "ses_ed1d5f0463764d7bb48dd69d37cd8bf7",
    "last_event_id": "evt_ca4f120fb928436ba9f65855ca6416e8"}


def filesystem_observation(path: str) -> dict[str, Any]:
    stat = os.stat(path)
    return {"path": path, "device_id": filesystem_device_id(path, stat),
            "inode": stat.st_ino, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def former_audio_path_status() -> list[dict[str, Any]]:
    return [{"path": path, "is_file": os.path.isfile(path), "lexists": os.path.lexists(path)}
            for path in (AUDIO_TARGET_PATH, AUDIO_LINK_PATH)]


def target_link_resolves() -> bool:
    return os.path.realpath(LINK_PATH) == TARGET_PATH


def _fixed_row(db, table, key, value, expected, label):
    actual = one(db, table, f"WHERE {key}=?", (value,))
    require(actual == expected, f"{label} changed")
    return actual


def _version_row(db, table, key, expected, label):
    actual = one(db, table, f"WHERE {key}=?", (expected[key],))
    require(actual is not None, f"{label} missing")
    for field, value in expected.items():
        if field not in ("last_seen", "valid_to"):
            require(actual[field] == value, f"{label} changed: {field}")
    require(actual["last_seen"] >= expected["last_seen"], f"{label} last_seen regressed")
    if actual["valid_to"] is not None:
        require(actual["valid_to"] >= actual["valid_from"], f"{label} invalid retirement")
    return actual


def _target_identity(row, device_id, *, nulled=False):
    expected_device = None if nulled else device_id
    expected_inode = None if nulled else TARGET_INODE
    return (row["device_id"], row["inode"], row["size"], row["mtime_ns"],
            row["fingerprint_algorithm"], row["fingerprint"]) == (
                expected_device, expected_inode, TARGET_SIZE, TARGET_MTIME_NS, None, None)


def _identity_evidence(db, observation):
    all_rows = rows(db, PREFIX + "file_identities", "WHERE asset_id IN (?,?,?)",
                    (WRONG_ASSET, CORRECT_ASSET, AUDIO_ASSET))
    by_id = {row["identity_id"]: row for row in all_rows}
    for identity_id, expected in EXPECTED_FIXED_IDENTITIES.items():
        actual = by_id.get(identity_id)
        if identity_id == WRONG_IDENTITY:
            require(actual == expected or actual == {**expected, "device_id": None, "inode": None},
                    "wrong Silo identity changed")
        else:
            require(actual == expected, f"fixed identity changed: {identity_id}")
    extras = [row for row in all_rows if row["identity_id"] not in EXPECTED_FIXED_IDENTITIES]
    wrong_extra = [row for row in extras if row["asset_id"] == WRONG_ASSET]
    correct_extra = [row for row in extras if row["asset_id"] == CORRECT_ASSET]
    require(not [row for row in extras if row["asset_id"] == AUDIO_ASSET],
            "unexpected audio identity")
    require(len(wrong_extra) <= 1 and all(
        _target_identity(row, observation["device_id"]) or _target_identity(
            row, observation["device_id"], nulled=True) for row in wrong_extra),
        "unreviewed wrong-source identity")
    require(len(correct_extra) <= 1 and all(
        _target_identity(row, observation["device_id"]) for row in correct_extra),
        "unreviewed canonical identity")
    wrong_target = [by_id[WRONG_IDENTITY], *wrong_extra]
    active = [row for row in wrong_target if row["device_id"] is not None]
    return {"rows": all_rows, "wrong_target": wrong_target,
            "wrong_status": RepairPhase.READY if active else RepairPhase.DONE,
            "canonical_stable": correct_extra[0] if correct_extra else None}


def _active_observation_status(db, table, source_rows, selector):
    fields = selector.split(",")
    current = []
    for source in source_rows:
        where = " AND ".join(f"{field}=?" for field in fields) + " AND valid_to IS NULL"
        current.append(one(db, table, f"WHERE {where}", tuple(source[field] for field in fields)))
    if all(row is not None and row["asset_id"] == WRONG_ASSET for row in current):
        require(current == source_rows, f"{table}: active source versions changed")
        return RepairPhase.READY, current
    if all(row is not None and row["asset_id"] == CORRECT_ASSET for row in current):
        ignored = {"location_id", "alias_id", "asset_id", "valid_from", "valid_to", "last_seen"}
        for source, replacement in zip(source_rows, current):
            for field in source.keys() - ignored:
                require(replacement[field] == source[field], f"{table}: replacement metadata changed")
            require(source["valid_to"] is not None and
                    replacement["valid_from"] == source["valid_to"],
                    f"{table}: replacement version boundary changed")
        return RepairPhase.DONE, current
    raise ChangedEvidence(f"{table}: partial or unexpected active ownership")


def _observation_evidence(db):
    source_locations = [_version_row(db, PREFIX + "locations", "location_id", row,
                                     "source location") for row in EXPECTED_SOURCE_LOCATIONS]
    source_aliases = [_version_row(db, PREFIX + "aliases", "alias_id", row,
                                  "source alias") for row in EXPECTED_SOURCE_ALIASES]
    prior_locations = [_fixed_row(db, PREFIX + "locations", "location_id", row["location_id"],
                                  row, "prior canonical location")
                       for row in EXPECTED_PRIOR_LOCATIONS]
    prior_aliases = [_fixed_row(db, PREFIX + "aliases", "alias_id", row["alias_id"],
                                row, "prior canonical alias") for row in EXPECTED_PRIOR_ALIASES]
    location_status, active_locations = _active_observation_status(
        db, PREFIX + "locations", source_locations, "path")
    alias_status, active_aliases = _active_observation_status(
        db, PREFIX + "aliases", source_aliases, "namespace,alias")
    return {"source_locations": source_locations, "source_aliases": source_aliases,
            "prior_locations": prior_locations, "prior_aliases": prior_aliases,
            "active_locations": active_locations, "active_aliases": active_aliases,
            "location_status": location_status, "alias_status": alias_status}


def _legacy_evidence(db):
    actual = one(db, PREFIX + "legacy_keys", "WHERE source=? AND media_key=?",
                 ("v1-progress", MEDIA_KEY))
    require(actual is not None, "legacy binding missing")
    for field in ("source", "media_key", "first_seen", "metadata_json"):
        require(actual[field] == EXPECTED_LEGACY[field], f"legacy binding changed: {field}")
    require(actual["last_seen"] >= EXPECTED_LEGACY["last_seen"], "legacy last_seen regressed")
    require(actual["asset_id"] in (AUDIO_ASSET, CORRECT_ASSET), "legacy binding target changed")
    return actual, RepairPhase.READY if actual["asset_id"] == AUDIO_ASSET else RepairPhase.DONE


def _counterpart_summary(db):
    result = [dict(row) for row in db.execute("""
        SELECT a.*, w.series FROM video_v2_assets a
        JOIN video_v2_works w USING(work_id)
        WHERE lower(w.series) = 'counterpart'
        ORDER BY a.work_id, a.asset_id
        """)]
    counts = Counter(row["work_id"] for row in result)
    duplicate_works = sum(count > 1 for count in counts.values())
    require({"duplicate_works": duplicate_works, "assets": len(result)} ==
            EXPECTED_COUNTERPART_COUNTS, "Counterpart duplicate evidence changed")
    return {"duplicate_works": duplicate_works, "assets": len(result),
            "asset_ids": [row["asset_id"] for row in result],
            "action": "retained; outside the guarded Silo repair"}


def _playback_evidence(db):
    states = rows(db, PREFIX + "asset_playback_state", "WHERE asset_id IN (?,?,?)",
                  (WRONG_ASSET, CORRECT_ASSET, AUDIO_ASSET))
    require(states == [EXPECTED_AUDIO_STATE], "Silo/audio playback state changed")
    sessions = rows(db, PREFIX + "playback_sessions", "WHERE asset_id=? ORDER BY started_at,session_id",
                    (AUDIO_ASSET,))
    events = rows(db, PREFIX + "playback_events", "WHERE asset_id=? ORDER BY observed_at,event_id",
                  (AUDIO_ASSET,))
    require({"sessions": len(sessions), "events": len(events)} == EXPECTED_AUDIO_HISTORY_COUNTS,
            "audio playback history count changed")
    progress = rows(db, "progress", "WHERE media_key=?", (MEDIA_KEY,))
    return {"states": states, "sessions": sessions, "events": events, "progress": progress}


def capture_evidence(db: sqlite3.Connection) -> dict[str, Any]:
    observation = filesystem_observation(TARGET_PATH)
    require((observation["inode"], observation["size"], observation["mtime_ns"]) ==
            (TARGET_INODE, TARGET_SIZE, TARGET_MTIME_NS), "Silo target stat changed")
    require(observation["device_id"] == FILESYSTEM_DEVICE_ID,
            "Silo filesystem identity changed")
    require(target_link_resolves(), "Silo link retargeted")
    absent = former_audio_path_status()
    require(not any(item["is_file"] for item in absent), "former audio file still exists")
    assets = rows(db, PREFIX + "assets", "WHERE asset_id IN (?,?,?) ORDER BY created_at",
                  (WRONG_ASSET, AUDIO_ASSET, CORRECT_ASSET))
    works = rows(db, PREFIX + "works", "WHERE work_id IN (?,?) ORDER BY created_at",
                 (BARBARIANS_WORK, SILO_WORK))
    require(equal_rows(assets, EXPECTED_ASSETS), "pinned asset evidence changed")
    require(equal_rows(works, EXPECTED_WORKS), "pinned work evidence changed")
    identities = _identity_evidence(db, observation)
    observations = _observation_evidence(db)
    legacy, legacy_status = _legacy_evidence(db)
    return {"target": observation, "former_audio_paths": absent, "assets": assets,
            "works": works, "identities": identities, "observations": observations,
            "legacy": legacy, "legacy_status": legacy_status,
            "playback": _playback_evidence(db),
            "counterpart_duplicates": _counterpart_summary(db)}


def build_plan(db: sqlite3.Connection) -> dict[str, Any]:
    return {"filesystem_identity_repair_version": PLAN_VERSION,
            "repair": "silo-s03e07-filesystem-identity",
            "pinned": {"wrong_asset": WRONG_ASSET, "correct_asset": CORRECT_ASSET,
                       "audio_asset": AUDIO_ASSET, "target_path": TARGET_PATH,
                       "link_path": LINK_PATH, "media_key": MEDIA_KEY,
                       "inode": TARGET_INODE, "size": TARGET_SIZE,
                       "mtime_ns": TARGET_MTIME_NS, "filesystem_device_id": FILESYSTEM_DEVICE_ID},
            "evidence": capture_evidence(db)}


def _validate_planned_version_rows(planned, expected, key, label):
    require(isinstance(planned, list) and len(planned) == len(expected),
            f"plan {label} count changed")
    by_id = {row[key]: row for row in planned}
    for old in expected:
        current = by_id.get(old[key])
        require(current is not None, f"plan {label} missing")
        for field, value in old.items():
            if field not in ("last_seen", "valid_to"):
                require(current[field] == value, f"plan {label} changed: {field}")
        require(current["last_seen"] >= old["last_seen"], f"plan {label} last_seen regressed")


def _validate_planned_identities(planned, target):
    require(isinstance(planned, list), "plan identities must be a list")
    by_id = {row["identity_id"]: row for row in planned}
    for identity_id, expected in EXPECTED_FIXED_IDENTITIES.items():
        current = by_id.get(identity_id)
        if identity_id == WRONG_IDENTITY:
            require(current == expected or current == {**expected, "device_id": None, "inode": None},
                    "plan wrong identity changed")
        else:
            require(current == expected, f"plan fixed identity changed: {identity_id}")
    extras = [row for row in planned if row["identity_id"] not in EXPECTED_FIXED_IDENTITIES]
    wrong = [row for row in extras if row["asset_id"] == WRONG_ASSET]
    correct = [row for row in extras if row["asset_id"] == CORRECT_ASSET]
    require(len(wrong) <= 1 and all(
        _target_identity(row, target["device_id"]) or
        _target_identity(row, target["device_id"], nulled=True) for row in wrong),
        "plan wrong-source promotion changed")
    require(len(correct) <= 1 and all(
        _target_identity(row, target["device_id"]) for row in correct),
        "plan canonical promotion changed")
    require(len(extras) == len(wrong) + len(correct), "plan includes an unreviewed identity")


def validate_plan_shape(plan: dict[str, Any]) -> None:
    require(plan.get("filesystem_identity_repair_version") == PLAN_VERSION,
            "unsupported repair plan version")
    require(plan.get("repair") == "silo-s03e07-filesystem-identity", "wrong repair plan")
    require(plan.get("pinned") == build_plan_pins(), "repair plan semantic pins changed")
    planned = plan.get("evidence")
    require(isinstance(planned, dict), "missing enriched evidence")
    target = planned.get("target")
    require(isinstance(target, dict) and target.get("path") == TARGET_PATH,
            "plan target path changed")
    require((target.get("inode"), target.get("size"), target.get("mtime_ns")) ==
            (TARGET_INODE, TARGET_SIZE, TARGET_MTIME_NS), "plan target stat changed")
    require(target.get("device_id") == FILESYSTEM_DEVICE_ID,
            "plan filesystem identity changed")
    require(equal_rows(planned.get("assets", []), EXPECTED_ASSETS), "plan assets changed")
    require(equal_rows(planned.get("works", []), EXPECTED_WORKS), "plan works changed")
    identities = planned.get("identities", {})
    _validate_planned_identities(identities.get("rows"), target)
    observations = planned.get("observations", {})
    _validate_planned_version_rows(observations.get("source_locations"),
                                   EXPECTED_SOURCE_LOCATIONS, "location_id", "location")
    _validate_planned_version_rows(observations.get("source_aliases"),
                                   EXPECTED_SOURCE_ALIASES, "alias_id", "alias")
    require(equal_rows(observations.get("prior_locations", []), EXPECTED_PRIOR_LOCATIONS),
            "plan prior locations changed")
    require(equal_rows(observations.get("prior_aliases", []), EXPECTED_PRIOR_ALIASES),
            "plan prior aliases changed")
    legacy = planned.get("legacy", {})
    for field in ("source", "media_key", "first_seen", "metadata_json"):
        require(legacy.get(field) == EXPECTED_LEGACY[field], f"plan legacy {field} changed")
    require(legacy.get("asset_id") in (AUDIO_ASSET, CORRECT_ASSET), "plan legacy target changed")
    playback = planned.get("playback", {})
    require(playback.get("states") == [EXPECTED_AUDIO_STATE], "plan playback state changed")
    require({"sessions": len(playback.get("sessions", [])),
             "events": len(playback.get("events", []))} == EXPECTED_AUDIO_HISTORY_COUNTS,
            "plan audio history count changed")


def build_plan_pins() -> dict[str, Any]:
    return {"wrong_asset": WRONG_ASSET, "correct_asset": CORRECT_ASSET,
            "audio_asset": AUDIO_ASSET, "target_path": TARGET_PATH, "link_path": LINK_PATH,
            "media_key": MEDIA_KEY, "inode": TARGET_INODE, "size": TARGET_SIZE,
            "mtime_ns": TARGET_MTIME_NS, "filesystem_device_id": FILESYSTEM_DEVICE_ID}


def _forward_version_rows(current, planned, key, label):
    current_by_id = {row[key]: row for row in current}
    for old in planned:
        now = current_by_id.get(old[key])
        require(now is not None, f"{label} disappeared")
        for field, value in old.items():
            if field not in ("last_seen", "valid_to"):
                require(now[field] == value, f"{label} changed: {field}")
        require(now["last_seen"] >= old["last_seen"], f"{label} last_seen regressed")
        if old["valid_to"] is not None:
            require(now["valid_to"] == old["valid_to"], f"{label} retirement changed")


def _forward_identity_rows(current, planned):
    current_by_id = {row["identity_id"]: row for row in current}
    for old in planned:
        now = current_by_id.get(old["identity_id"])
        require(now is not None, "planned identity disappeared")
        if old["asset_id"] == WRONG_ASSET and _target_identity(old, old["device_id"]):
            require(now == old or now == {**old, "device_id": None, "inode": None},
                    "wrong identity changed outside allowed invalidation")
        else:
            require(now == old, "planned identity changed")


def validate_against_plan(db: sqlite3.Connection, plan: dict[str, Any]) -> dict[str, Any]:
    validate_plan_shape(plan)
    current = capture_evidence(db)
    planned = plan["evidence"]
    require(current["target"] == planned["target"], "target observation differs from plan")
    require(equal_rows(current["assets"], planned["assets"]), "assets differ from plan")
    require(equal_rows(current["works"], planned["works"]), "works differ from plan")
    _forward_identity_rows(current["identities"]["rows"], planned["identities"]["rows"])
    for key, row_key, label in (("source_locations", "location_id", "source location"),
                                ("source_aliases", "alias_id", "source alias")):
        _forward_version_rows(current["observations"][key], planned["observations"][key],
                              row_key, label)
    for key in ("prior_locations", "prior_aliases"):
        require(equal_rows(current["observations"][key], planned["observations"][key]),
                f"{key} differ from plan")
    require(equal_rows(current["playback"]["states"], planned["playback"]["states"]),
            "playback state differs from plan")
    for key in ("sessions", "events", "progress"):
        require(equal_rows(current["playback"][key], planned["playback"][key]),
                f"{key} differ from plan")
    old_legacy, new_legacy = planned["legacy"], current["legacy"]
    for field in ("source", "media_key", "first_seen", "metadata_json"):
        require(new_legacy[field] == old_legacy[field], f"legacy {field} differs from plan")
    require(new_legacy["last_seen"] >= old_legacy["last_seen"], "legacy last_seen regressed")
    if old_legacy["asset_id"] == CORRECT_ASSET:
        require(new_legacy["asset_id"] == CORRECT_ASSET, "legacy binding regressed")
    return current
