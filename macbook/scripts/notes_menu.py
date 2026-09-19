#!/usr/bin/env python3
"""Read Apple Notes titles/IDs, read-only, for BTT recent/pinned dropdowns.

Only metadata is queried: never note bodies, attachments, or decryption fields.
Notes' private schema is checked before use and may need adapting after macOS
updates. Actual note opening uses the existing Notes AppleScript helper.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shlex
import sqlite3
import sys
import uuid


DATABASE = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"
OPENER = Path(__file__).resolve().with_name("show_note.sh")
ROW_BACKGROUND = "#2B2D31"
ROW_TEXT = "#EEEDE9"
ROW_BACKGROUND_RGBA = "43, 45, 49, 255"
ROW_HOVER_RGBA = "54, 57, 63, 255"
ROW_TEXT_RGBA = "238, 237, 233, 255"


def menu_row(title: str, icon: str = "note.text") -> dict:
    """Style each dynamic row, rather than inheriting BTT's default palette.

    BTT supports full item properties alongside Simple JSON's title/icon/action:
    https://community.folivora.ai/t/floating-menu-w-content-script-text-formating-issue/44210/3
    Keep these flat on the item, not nested in BTTMenuConfig. No input, action,
    positioning, or size settings are changed here.
    """
    item = {
        "title": {"text": title, "size": 16, "color": ROW_TEXT},
        "icon": f"sfsymbol::{icon}::color@@{ROW_TEXT}",
        "background": ROW_BACKGROUND,
        "BTTMenuItemShowHoverEffect": 1,
    }
    for suffix in ("", "Dark"):
        for name, value in {
            "BackgroundType": 4,
            "BackgroundColor": ROW_BACKGROUND_RGBA,
            "BackgroundColorHover": ROW_HOVER_RGBA,
            "FontColor": ROW_TEXT_RGBA,
            "FontColorHover": ROW_TEXT_RGBA,
            "IconColor1": ROW_TEXT_RGBA,
            "IconColor1Hover": ROW_TEXT_RGBA,
            "BorderColor": "0, 0, 0, 0",
            "BorderColorHover": "0, 0, 0, 0",
            "BlurredBackground": 0,
            "GlassEffect": 0,
        }.items():
            item[f"BTTMenuItem{name}{suffix}"] = value
    return item


def read_notes(database: Path, mode: str, limit: int | None = None) -> list[dict]:
    if mode not in ("recent", "pinned"):
        raise ValueError("Unknown Notes menu mode")
    if limit is not None and not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")  # Keep metadata and rows in one read snapshot.
        columns = {r[1] for r in connection.execute("PRAGMA table_info(ZICCLOUDSYNCINGOBJECT)")}
        required = {"Z_PK", "Z_ENT", "ZTITLE1", "ZTITLE2", "ZFOLDER", "ZFOLDERTYPE", "ZISPINNED",
                    "ZMARKEDFORDELETION", "ZMODIFICATIONDATE1"}
        if not required.issubset(columns):
            raise ValueError("Apple Notes database format changed")
        entity = connection.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME='ICNote'").fetchone()
        metadata = connection.execute("SELECT Z_UUID FROM Z_METADATA").fetchall()
        if entity is None or len(metadata) != 1:
            raise ValueError("Cannot identify the Apple Notes store")
        store = str(uuid.UUID(metadata[0][0])).upper()
        query = """
            SELECT n.Z_PK AS pk, n.ZTITLE1 AS title, f.ZTITLE2 AS folder
            FROM ZICCLOUDSYNCINGOBJECT AS n
            JOIN ZICCLOUDSYNCINGOBJECT AS f ON f.Z_PK=n.ZFOLDER
            WHERE n.Z_ENT=? AND COALESCE(n.ZMARKEDFORDELETION,0)=0
              AND COALESCE(f.ZMARKEDFORDELETION,0)=0
              AND COALESCE(f.ZFOLDERTYPE,0)<>1
              AND n.ZTITLE1 IS NOT NULL AND trim(n.ZTITLE1)<>''
        """
        args = [entity[0]]
        if mode == "pinned":
            query += " AND n.ZISPINNED=1"
        query += " ORDER BY COALESCE(n.ZMODIFICATIONDATE1,0) DESC, n.Z_PK DESC"
        if limit is not None:
            query += " LIMIT ?"
            args.append(limit)
        return [{"title": row["title"], "folder": row["folder"] or "",
                 "id": f"x-coredata://{store}/ICNote/p{int(row['pk'])}"}
                for row in connection.execute(query, args)]
    finally:
        connection.close()


def menu_items(notes: list[dict], mode: str, opener: Path = OPENER) -> list[dict]:
    if not notes:
        return [menu_row("No pinned notes" if mode == "pinned" else "No recent notes")]
    titles = [" ".join(note["title"].splitlines()) for note in notes]
    duplicates = Counter(titles)
    items = []
    for note, title in zip(notes, titles):
        if duplicates[title] > 1 and note["folder"]:
            title += " — " + " ".join(note["folder"].splitlines())
        command = shlex.join(["/bin/bash", str(opener), "--id", note["id"]])
        item = menu_row(title)
        item["action"] = {"js": "runShellScript({script: " + json.dumps(command) + "})"}
        items.append(item)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("recent", "pinned"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--check", action="store_true", help="validate access and print counts only")
    args = parser.parse_args()
    limit = args.limit if args.limit is not None else (10 if args.mode == "recent" else None)
    try:
        if not OPENER.is_file():
            raise ValueError("Notes opening helper is missing")
        notes = read_notes(args.database, args.mode, limit)
    except (OSError, sqlite3.Error, ValueError) as error:
        print(f"Notes menu: {error}", file=sys.stderr)
        if args.check:
            return 1
        print(json.dumps([menu_row("Notes unavailable — check BTT Full Disk Access",
                                  "exclamationmark.triangle")]))
        return 0
    if args.check:
        print(json.dumps({"mode": args.mode, "count": len(notes), "database_read_only": True}))
    else:
        print(json.dumps(menu_items(notes, args.mode), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
