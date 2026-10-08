"""Physical-file observations used by the media asset catalog."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .catalog_values import CatalogConflict, _new_id
from .filesystem_identity import UUID_PREFIX, verified_legacy_path

if TYPE_CHECKING:
    from .catalog import MediaAssetCatalog


def _accepts_file_stat(
    catalog: MediaAssetCatalog,
    db: sqlite3.Connection,
    asset_id: str,
    stat_asset_ids: set[str],
) -> bool:
    if asset_id in stat_asset_ids:
        return True
    # The indexed device/inode lookup already found every exact match. Only
    # path-only seeds need the fallback; stop at the first contrary observation.
    return catalog._one(
        db,
        """
        SELECT 1 FROM video_v2_file_identities
        WHERE asset_id = ? AND device_id IS NOT NULL AND inode IS NOT NULL
        LIMIT 1
        """,
        (asset_id,),
    ) is None


def _legacy_path_matches(catalog, db, path, asset_id, device_id, inode, size, mtime_ns):
    """Promote only a still-active exact path, never retired device-number history."""
    if not device_id or not device_id.startswith(UUID_PREFIX):
        return False
    if inode is None or size is None or mtime_ns is None:
        return False
    prior = catalog._all(db, """
        SELECT device_id, inode, size, mtime_ns FROM video_v2_file_identities
        WHERE asset_id = ? AND device_id IS NOT NULL AND inode IS NOT NULL
        """, (asset_id,))
    if any(str(row["device_id"]).startswith(UUID_PREFIX) for row in prior):
        return False  # Old numeric rows cannot override a known filesystem UUID.
    matching = any(
        str(row["device_id"]).isdecimal()
        and (row["inode"], row["size"], row["mtime_ns"]) == (inode, size, mtime_ns)
        for row in prior
    )
    return matching and verified_legacy_path(path, device_id, inode, size, mtime_ns)


def _stat_assets(catalog, db, device_id, inode, size, mtime_ns):
    if device_id is None or inode is None or size is None:
        return set()
    stable = str(device_id).startswith(UUID_PREFIX)
    rows = catalog._all(db, """
        SELECT DISTINCT f.asset_id FROM video_v2_file_identities f
        WHERE f.device_id = ? AND f.inode = ? AND f.size = ?
          AND (f.mtime_ns = ? OR (? AND (? IS NULL OR (
            f.mtime_ns IS NULL AND NOT EXISTS (
              SELECT 1 FROM video_v2_file_identities timed
              WHERE timed.device_id = f.device_id AND timed.inode = f.inode
                AND timed.asset_id = f.asset_id AND timed.size = f.size
                AND timed.mtime_ns IS NOT NULL
            )
          ))))
          AND (? OR NOT EXISTS (
            SELECT 1 FROM video_v2_file_identities known
            WHERE known.asset_id = f.asset_id AND known.device_id LIKE ?
          ))
        """, (str(device_id), int(inode), int(size), mtime_ns, stable, mtime_ns,
              stable, UUID_PREFIX + "%"))
    return {str(row["asset_id"]) for row in rows}


def matching_file_assets(
    catalog: MediaAssetCatalog,
    db: sqlite3.Connection,
    *,
    path: str,
    device_id: str | None,
    inode: int | None,
    size: int | None,
    fingerprint_algorithm: str | None,
    fingerprint: str | None,
    preferred_asset_id: str | None,
    mtime_ns: int | None = None,
) -> set[str]:
    asset_ids: set[str] = set()
    path_row = catalog._one(
        db,
        "SELECT asset_id FROM video_v2_locations WHERE path = ? AND valid_to IS NULL",
        (path,),
    )
    stat_asset_ids = _stat_assets(catalog, db, device_id, inode, size, mtime_ns)
    if (path_row is not None and str(path_row["asset_id"]) not in stat_asset_ids
            and _legacy_path_matches(
                catalog, db, path, str(path_row["asset_id"]), device_id, inode, size, mtime_ns
            )):
        stat_asset_ids.add(str(path_row["asset_id"]))
    asset_ids.update(stat_asset_ids)
    if path_row is not None:
        path_asset_id = str(path_row["asset_id"])
        # Locations are versioned only after all observations agree, including
        # when the caller owns the surrounding scan transaction.
        if _accepts_file_stat(catalog, db, path_asset_id, stat_asset_ids):
            asset_ids.add(path_asset_id)
    if fingerprint is not None:
        rows = catalog._all(
            db,
            """
            SELECT DISTINCT asset_id FROM video_v2_file_identities
            WHERE fingerprint_algorithm = ? AND fingerprint = ?
              AND (size = ? OR size IS NULL OR ? IS NULL)
            """,
            (fingerprint_algorithm, fingerprint, size, size),
        )
        asset_ids.update(str(row["asset_id"]) for row in rows)
    if not asset_ids and preferred_asset_id is not None:
        catalog._assert_asset(db, preferred_asset_id)
        if _accepts_file_stat(catalog, db, preferred_asset_id, stat_asset_ids):
            asset_ids.add(preferred_asset_id)
    if len(asset_ids) > 1:
        raise CatalogConflict("file observations resolve to multiple assets")
    return asset_ids


def record_file_identity(
    catalog: MediaAssetCatalog,
    db: sqlite3.Connection,
    *,
    asset_id: str,
    device_id: str | None,
    inode: int | None,
    size: int | None,
    mtime_ns: int | None,
    fingerprint_algorithm: str | None,
    fingerprint: str | None,
    observed_at: float,
) -> None:
    identity_match = catalog._one(
        db,
        """
        SELECT identity_id FROM video_v2_file_identities
        WHERE asset_id = ?
          AND device_id IS ? AND inode IS ? AND size IS ? AND mtime_ns IS ?
          AND fingerprint_algorithm IS ? AND fingerprint IS ?
        ORDER BY observed_at DESC LIMIT 1
        """,
        (
            asset_id,
            str(device_id) if device_id is not None else None,
            int(inode) if inode is not None else None,
            int(size) if size is not None else None,
            int(mtime_ns) if mtime_ns is not None else None,
            fingerprint_algorithm,
            fingerprint,
        ),
    )
    if identity_match is None:
        db.execute(
            """
            INSERT INTO video_v2_file_identities
                (identity_id, asset_id, device_id, inode, size, mtime_ns,
                 fingerprint_algorithm, fingerprint, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("fid"),
                asset_id,
                str(device_id) if device_id is not None else None,
                int(inode) if inode is not None else None,
                int(size) if size is not None else None,
                int(mtime_ns) if mtime_ns is not None else None,
                fingerprint_algorithm,
                fingerprint,
                observed_at,
            ),
        )
