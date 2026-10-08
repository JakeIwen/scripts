"""Reviewed same-file proof and runtime preconditions for legacy-key churn."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, TypedDict

from pi.apps.video_library.file_observations import matching_file_assets
from pi.apps.video_library.maintenance import identity_repair_evidence as silo
from pi.apps.video_library.maintenance.same_file_repair import (
    equal_rows, one, require, rows,
)

PREFIX = "video_v2_"
PRIOR_PLAN_SHA256 = "b3933166566ec43d45ebc98791c7290983b6c167c0e009f78add28b8f2fdea5e"
PRIOR_PLAN_PATH = Path("/home/pi/.local/share/van-video-library/repair-audit-20261007/plan.json")
REASON = "reviewed device-churn legacy repair"
PHYSICAL_FIELDS = ("inode", "size", "mtime_ns")
PLAYHEAD_FIELDS = ("position", "duration", "completed")


class ProofKind(str, Enum):
    PRIOR_REPAIR = "prior-repair"
    COUNTERPART = "counterpart"


EXPECTED_COUNTS = {ProofKind.PRIOR_REPAIR: 2013, ProofKind.COUNTERPART: 10}


@dataclass(frozen=True)
class PriorRepair:
    sha256: str
    by_target: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class IdentityIndex:
    """Transaction-local evidence; legacy-key repair never mutates identity rows."""

    by_asset: dict[str, list[dict[str, Any]]]

    @classmethod
    def read(cls, db):
        indexed = {}
        for row in rows(db, PREFIX + "file_identities", "ORDER BY identity_id"):
            indexed.setdefault(row["asset_id"], []).append(row)
        return cls(indexed)

    def for_assets(self, source, target):
        return sorted([*self.by_asset.get(source, []), *self.by_asset.get(target, [])],
                      key=lambda row: row["identity_id"])


class ChurnPair(TypedDict):
    kind: str
    media_key: str
    title: str
    rel_path: str
    path: str
    real_path: str
    source_asset: str
    target_asset: str
    observation: dict[str, Any]
    legacy: dict[str, Any]
    assets: list[dict[str, Any]]
    identities: list[dict[str, Any]]
    locations: list[dict[str, Any]]
    work: dict[str, Any]
    work_state: dict[str, Any] | None
    source_state: dict[str, Any] | None
    target_state: dict[str, Any] | None
    progress: dict[str, Any] | None
    shadow: dict[str, Any] | None
    transfer: bool


def load_prior_plan(path: Path) -> PriorRepair:
    raw = path.read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    require(checksum == PRIOR_PLAN_SHA256, "original reviewed repair plan checksum changed")
    plan = json.loads(raw)
    require(plan.get("repair_input_version") == 1, "original repair plan version changed")
    pairs = plan["same_file_pairs"]
    indexed = {pair["target_asset"]: pair for pair in pairs}
    require(len(indexed) == len(pairs), "ambiguous original repair targets")
    return PriorRepair(checksum, indexed)


def current_candidate(catalog, db, item):
    observation = silo.filesystem_observation(item.real_path)
    require(observation["device_id"] == silo.FILESYSTEM_DEVICE_ID,
            "legacy repair filesystem changed")
    candidates = matching_file_assets(
        catalog, db, path=item.real_path, device_id=observation["device_id"],
        inode=observation["inode"], size=observation["size"],
        mtime_ns=observation["mtime_ns"], fingerprint_algorithm=None, fingerprint=None,
        preferred_asset_id=None,
    )
    require(len(candidates) == 1, "legacy repair item does not have one physical owner")
    return observation, next(iter(candidates))


def playhead_transfer_needed(source, target):
    if source is None:
        return False
    different = target is None or any(source[key] != target[key] for key in PLAYHEAD_FIELDS)
    more_plays = target is None or source["play_count"] > target["play_count"]
    if not (different or more_plays):
        return False  # Timestamp-only copies already represent the same progress.
    if target is not None:
        require(source["updated_at"] != target["updated_at"],
                "different playheads have equal timestamps; review required")
        if source["updated_at"] < target["updated_at"]:
            return False
    return True


def _asset_rows(db, source, target):
    return rows(db, PREFIX + "assets", "WHERE asset_id IN (?,?) ORDER BY asset_id",
                (source, target))


def _pair_snapshot(db, source, target, key, identities: IdentityIndex):
    assets = _asset_rows(db, source, target)
    require(len(assets) == 2 and assets[0]["work_id"] is not None and
            assets[0]["work_id"] == assets[1]["work_id"], "legacy repair work differs")
    work_id = assets[0]["work_id"]
    return {
        "assets": assets,
        "identities": identities.for_assets(source, target),
        "locations": rows(db, PREFIX + "locations",
                          "WHERE asset_id IN (?,?) ORDER BY location_id", (source, target)),
        "work": one(db, PREFIX + "works", "WHERE work_id=?", (work_id,)),
        "work_state": one(db, PREFIX + "work_watch_state", "WHERE work_id=?", (work_id,)),
        "source_state": one(db, PREFIX + "asset_playback_state", "WHERE asset_id=?", (source,)),
        "target_state": one(db, PREFIX + "asset_playback_state", "WHERE asset_id=?", (target,)),
        "progress": one(db, "progress", "WHERE media_key=?", (key,)),
        "shadow": one(db, PREFIX + "v1_shadow", "WHERE media_key=?", (key,)),
    }


def _same_physical_source(pair):
    observation = pair["observation"]
    source_ids = [row for row in pair["identities"] if row["asset_id"] == pair["source_asset"]
                  and row["device_id"] is not None and row["inode"] is not None]
    require(source_ids and all(all(row[key] == observation[key] for key in PHYSICAL_FIELDS)
                               for row in source_ids), "legacy source physical evidence differs")
    require(any(row["asset_id"] == pair["source_asset"] and row["path"] == pair["real_path"]
                for row in pair["locations"]), "legacy source lacks exact historical path")
    require(any(row["asset_id"] == pair["target_asset"] and row["path"] == pair["real_path"]
                and row["valid_to"] is None for row in pair["locations"]),
            "canonical asset lacks active exact path")


def validate_proof(pair: ChurnPair, prior: PriorRepair):
    _same_physical_source(pair)
    original = prior.by_target.get(pair["source_asset"])
    if original is not None:
        require(pair["kind"] == ProofKind.PRIOR_REPAIR, "original repair classification changed")
        require(pair["target_asset"] not in (original["source_asset"], original["target_asset"]),
                "expected third, August-era physical asset")
        require(pair["real_path"] == original["target_path"] and
                pair["work"]["work_id"] == original["work_id"], "original repair path/work changed")
        require(all(pair["observation"][key] == original["expected_stat"][key]
                    for key in PHYSICAL_FIELDS), "original repair physical proof changed")
        require(any(row["identity_id"] == original["identity_id"] and
                    row["asset_id"] == pair["source_asset"] for row in pair["identities"]),
                "original repair identity missing")
    else:
        work = pair["work"]
        require(pair["kind"] == ProofKind.COUNTERPART and
                str(work["series"]).casefold() == "counterpart" and work["season"] == 1 and
                work["episode"] in range(1, 11), "unreviewed legacy mismatch")
        by_id = {row["asset_id"]: row for row in pair["assets"]}
        require(by_id[pair["source_asset"]]["created_at"] <
                by_id[pair["target_asset"]]["created_at"], "Counterpart chronology changed")


def capture_churn_plan(db, library, catalog, prior: PriorRepair):
    identities = IdentityIndex.read(db)
    items, _ = library.snapshot()
    pairs = []
    for item in items:
        if item.key == silo.MEDIA_KEY:
            continue
        legacy = one(db, PREFIX + "legacy_keys", "WHERE source=? AND media_key=?",
                     ("v1-progress", item.key))
        observation, target = current_candidate(catalog, db, item)
        if legacy is None or legacy["asset_id"] == target:
            continue
        source = legacy["asset_id"]
        pair = {
            "kind": ProofKind.PRIOR_REPAIR if source in prior.by_target else ProofKind.COUNTERPART,
            "media_key": item.key, "title": item.title, "rel_path": item.rel_path,
            "path": item.path, "real_path": item.real_path, "source_asset": source,
            "target_asset": target, "observation": observation, "legacy": legacy,
            **_pair_snapshot(db, source, target, item.key, identities),
        }
        pair["transfer"] = playhead_transfer_needed(pair["source_state"], pair["target_state"])
        validate_proof(pair, prior)
        pairs.append(pair)
    result = {"version": 1, "prior_plan_sha256": prior.sha256, "pairs": pairs}
    validate_churn_plan(result, prior)
    return result


def validate_churn_plan(plan, prior: PriorRepair):
    require(isinstance(plan, dict) and plan.get("version") == 1, "missing legacy-churn plan")
    require(plan.get("prior_plan_sha256") == prior.sha256, "legacy plan audit provenance changed")
    pairs = plan.get("pairs")
    require(isinstance(pairs, list), "legacy plan pairs must be a list")
    require(Counter(pair["kind"] for pair in pairs) == Counter(EXPECTED_COUNTS),
            "legacy mismatch counts changed; capture and review fresh evidence")
    require(len({pair["media_key"] for pair in pairs}) == len(pairs), "duplicate legacy keys")
    sources, targets = ({pair[key] for pair in pairs} for key in ("source_asset", "target_asset"))
    require(not sources & targets and len(targets) == len(pairs), "overlapping legacy repair assets")
    for pair in pairs:
        require(set(pair) == set(ChurnPair.__annotations__), "legacy pair fields changed")
        require(pair["legacy"]["source"] == "v1-progress" and
                pair["legacy"]["media_key"] == pair["media_key"] and
                pair["legacy"]["asset_id"] == pair["source_asset"], "legacy plan binding changed")
        require(pair["transfer"] is playhead_transfer_needed(pair["source_state"], pair["target_state"]),
                "legacy plan playhead decision changed")
        validate_proof(pair, prior)


def _unchanged_locations(actual, expected):
    by_id = {row["location_id"]: row for row in actual}
    require(len(by_id) == len(expected), "legacy location versions changed")
    for old in expected:
        now = by_id.get(old["location_id"])
        require(now is not None and {k: v for k, v in now.items() if k != "last_seen"} ==
                {k: v for k, v in old.items() if k != "last_seen"}, "legacy location changed")
        require(now["last_seen"] >= old["last_seen"], "legacy location last_seen regressed")


def validate_pair(db, pair: ChurnPair, item, catalog, identities: IdentityIndex):
    require(item is not None and (item.path, item.real_path, item.title, item.rel_path) ==
            tuple(pair[key] for key in ("path", "real_path", "title", "rel_path")),
            "legacy library selection changed")
    observation, target = current_candidate(catalog, db, item)
    require(observation == pair["observation"] and target == pair["target_asset"],
            "legacy physical owner changed")
    current = _pair_snapshot(db, pair["source_asset"], target, pair["media_key"], identities)
    for field in ("assets", "identities"):
        require(equal_rows(current[field], pair[field]), f"legacy {field} changed")
    _unchanged_locations(current["locations"], pair["locations"])
    for field in ("work", "work_state", "source_state"):
        require(current[field] == pair[field], f"legacy {field} changed")
    binding = one(db, PREFIX + "legacy_keys", "WHERE source=? AND media_key=?",
                  ("v1-progress", pair["media_key"]))
    require(binding is not None, "legacy binding disappeared")
    for key, value in pair["legacy"].items():
        if key not in ("asset_id", "last_seen"):
            require(binding[key] == value, f"legacy binding changed: {key}")
    require(binding["last_seen"] >= pair["legacy"]["last_seen"], "legacy binding time regressed")
    require(binding["asset_id"] in (pair["source_asset"], target), "legacy binding owner changed")
    done = binding["asset_id"] == target
    if not done:
        for field in ("target_state", "progress", "shadow"):
            require(current[field] == pair[field], f"legacy {field} changed")
    return done, current
