"""Physical-file observations used by the media asset catalog."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .catalog_values import CatalogConflict, _new_id

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
) -> set[str]:
    asset_ids: set[str] = set()
    path_row = catalog._one(
        db,
        "SELECT asset_id FROM video_v2_locations WHERE path = ? AND valid_to IS NULL",
        (path,),
    )
    stat_asset_ids: set[str] = set()
    if device_id is not None and inode is not None:
        rows = catalog._all(
            db,
            """
            SELECT DISTINCT asset_id FROM video_v2_file_identities
            WHERE device_id = ? AND inode = ?
            """,
            (str(device_id), int(inode)),
        )
        stat_asset_ids.update(str(row["asset_id"]) for row in rows)
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
          AND device_id IS ? AND inode IS ? AND size IS ?
          AND fingerprint_algorithm IS ? AND fingerprint IS ?
        ORDER BY observed_at DESC LIMIT 1
        """,
        (
            asset_id,
            str(device_id) if device_id is not None else None,
            int(inode) if inode is not None else None,
            int(size) if size is not None else None,
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
