#!/usr/bin/env python3
"""Build importable vertical dropdown replacements for Media submenus.

The result contains one top-level floating-menu pack and one Media launcher
button pack. Each generated entity gets a fresh UUID, so the current submenus
can remain enabled while the replacements are tested.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from create_floating_dropdown_from_submenu import (
    MEDIA_UUID,
    load_single_trigger,
    make_dropdown,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RECENT_NOTES_COMMAND = (
    "/usr/bin/osascript -l JavaScript "
    "/Users/jacobr/dev/scripts/macbook/scripts/recent_notes_menu.js "
    "10 --no-back"
)


@dataclass(frozen=True)
class Dropdown:
    stem: str
    source: Path
    name: str
    width: int
    old_uuid: str
    height_items: int | None = None
    content_script_command: str | None = None


DROPDOWNS = (
    Dropdown(
        "speaker",
        REPO_ROOT / "tmp/floating_submenu.json",
        "Media — Speaker",
        210,
        "F3CC5F44-0D08-49DD-8B5C-3D6C1C022759",
    ),
    Dropdown(
        "emoji",
        REPO_ROOT / "tmp/emoji_floating_submenu.json",
        "Media — Emoji",
        100,
        "3C7CCA4E-B883-47D5-8A9D-143BD4C2B6F2",
    ),
    Dropdown(
        "addresses",
        REPO_ROOT / "tmp/addr_floating_submenu.json",
        "Media — Addresses",
        200,
        "75C1EC53-E8CA-4583-B4C2-108564BB18C3",
    ),
    Dropdown(
        "spktv",
        REPO_ROOT / "tmp/spktv_floating_submenu.json",
        "Media — Spktv",
        220,
        "1FD3C982-6A43-415A-BD06-F6E7C253A859",
    ),
    Dropdown(
        "recent_notes",
        REPO_ROOT / "tmp/recent_notes_floating_submenu.json",
        "Media — Recent Notes",
        320,
        "0F8AEB59-60C1-4B1F-A356-26AD45E3A0A3",
        height_items=10,
        content_script_command=RECENT_NOTES_COMMAND,
    ),
    Dropdown(
        "find_my",
        REPO_ROOT / "tmp/findmy_floating_submenu.json",
        "Media — Find My",
        180,
        "70C72D0E-14A4-4CCB-B0D3-A323BD4EFC57",
    ),
)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def collect_uuids(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        entity_uuid = value.get("BTTUUID")
        if isinstance(entity_uuid, str):
            result.append(entity_uuid)
        for child in value.values():
            result.extend(collect_uuids(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(collect_uuids(child))
    return result


def validate(menus: list[dict[str, Any]], buttons: list[dict[str, Any]]) -> None:
    all_uuids = collect_uuids(menus) + collect_uuids(buttons)
    if len(all_uuids) != len(set(all_uuids)):
        raise ValueError("generated duplicate BTT UUIDs")

    menu_ids = {menu["BTTUUID"] for menu in menus}
    for menu in menus:
        if menu.get("BTTTriggerType") != 767:
            raise ValueError("top-level dropdown is not trigger type 767")
        if menu.get("BTTMenuConfig", {}).get("BTTMenuLayoutDirection") != 6:
            raise ValueError("top-level dropdown is not vertical")

    for button in buttons:
        if button.get("BTTTriggerType") != 773:
            raise ValueError("launcher is not a standard Media item")
        if button.get("BTTTriggerParentUUID") != MEDIA_UUID:
            raise ValueError("launcher has the wrong Media parent")
        if "BTTOrder" in button:
            raise ValueError("launcher unexpectedly specifies an insertion index")
        config = button.get("BTTMenuConfig", {})
        if config.get("BTTMenuCategoryContentScript"):
            raise ValueError("content script leaked onto a launcher button")
        actions = button.get("BTTMenuItemActions", [])
        if len(actions) != 1 or actions[0].get("BTTPredefinedActionType") != 386:
            raise ValueError("launcher does not contain exactly one Show Menu action")
        target = actions[0].get("BTTAdditionalActionData", {}).get(
            "BTTMenuActionMenuID"
        )
        if target not in menu_ids:
            raise ValueError("launcher targets an unknown generated menu")


def readme_text(builds: list[tuple[Dropdown, str]]) -> str:
    old_rows = "\n".join(
        f"- {entry.name}: `{entry.old_uuid}`" for entry, _ in builds
    )
    generated_rows = "\n".join(
        f"- {entry.name}: `{menu_uuid}`" for entry, menu_uuid in builds
    )
    return f"""# BTT Media vertical dropdowns

These are replacement launchers plus standalone vertical floating menus. They
use fresh UUIDs and do not modify the existing Media submenus.

Import:

1. In **For All Apps → Custom Floating Menus**, paste
   `01_top_level_menus_ALL.json` into **Groups & Top Level Triggers**.
2. Select **Floating Menu: Media**, then paste
   `02_media_buttons_ALL.json` into its item list. The buttons omit `BTTOrder`,
   so BTT appends them.
3. Test all six new buttons.
4. Only after testing, disable the old submenu entries in Media.

Generated standalone menus:

{generated_rows}

Old submenu UUIDs retained for rollback/reference:

{old_rows}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "tmp/btt_vertical_dropdowns_current",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    all_menus: list[dict[str, Any]] = []
    all_buttons: list[dict[str, Any]] = []
    builds: list[tuple[Dropdown, str]] = []

    for entry in DROPDOWNS:
        source = load_single_trigger(entry.source)
        menus, buttons = make_dropdown(
            source,
            dropdown_name=entry.name,
            parent_uuid=MEDIA_UUID,
            width=entry.width,
            height_items=entry.height_items,
            content_script_command=entry.content_script_command,
        )
        write_json(args.output_dir / f"{entry.stem}_menu.json", menus)
        write_json(args.output_dir / f"{entry.stem}_button.json", buttons)
        all_menus.extend(menus)
        all_buttons.extend(buttons)
        builds.append((entry, menus[0]["BTTUUID"]))

    validate(all_menus, all_buttons)
    write_json(args.output_dir / "01_top_level_menus_ALL.json", all_menus)
    write_json(args.output_dir / "02_media_buttons_ALL.json", all_buttons)
    (args.output_dir / "README.md").write_text(
        readme_text(builds),
        encoding="utf-8",
    )

    print(args.output_dir)


if __name__ == "__main__":
    main()
