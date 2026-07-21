#!/usr/bin/env python3
"""Convert an exported BTT floating submenu into a standalone dropdown.

The output is deliberately split into two clipboard-importable JSON files:

1. A hidden, vertical top-level floating menu containing cloned submenu items.
2. A standard item that shows that menu and can be pasted into the parent menu.

All entity UUIDs are regenerated so the source submenu can remain configured
(but disabled) without colliding with the trial dropdown.
"""

from __future__ import annotations

import argparse
import copy
import json
import time
import uuid
from pathlib import Path
from typing import Any


MEDIA_UUID = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"


def new_uuid() -> str:
    return str(uuid.uuid4()).upper()


def load_single_trigger(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError(f"expected one trigger in {path}, found {len(value)}")
        value = value[0]
    if not isinstance(value, dict) or value.get("BTTTriggerType") != 774:
        raise ValueError(f"{path} is not a single floating submenu (type 774)")
    if not isinstance(value.get("BTTMenuItems"), list):
        raise ValueError(f"{path} has no BTTMenuItems array")
    return value


def remap_entity_uuids(value: Any) -> Any:
    """Replace every entity UUID and all exact references to those UUIDs."""
    replacements: dict[str, str] = {}

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            entity_uuid = node.get("BTTUUID")
            if isinstance(entity_uuid, str):
                replacements.setdefault(entity_uuid, new_uuid())
            for child in node.values():
                collect(child)
        elif isinstance(node, list):
            for child in node:
                collect(child)

    def replace(node: Any) -> Any:
        if isinstance(node, dict):
            result = {key: replace(child) for key, child in node.items()}
            if isinstance(result.get("BTTLastChangeUUID"), str):
                result["BTTLastChangeUUID"] = new_uuid()
            return result
        if isinstance(node, list):
            return [replace(child) for child in node]
        if isinstance(node, str):
            return replacements.get(node, node)
        return node

    collect(value)
    return replace(value)


def dropdown_menu_config(identifier: str, item_count: int, width: int) -> dict[str, Any]:
    item_height = 40
    height = max(item_height, item_height * item_count)
    return {
        "BTTMenuPositioningType": 1,
        "BTTMenuPositionRelativeTo": 21,
        "BTTMenuAnchorMenu": 0,
        "BTTMenuAnchorRelation": 2,
        "BTTMenuOffsetX": 0,
        "BTTMenuOffsetY": -4,
        "BTTMenuOffsetXUnit": 0,
        "BTTMenuOffsetYUnit": 0,
        "BTTMenuFrameWidth": width,
        "BTTMenuFrameHeight": height,
        "BTTMenuLayoutDirection": 6,
        "BTTMenuHorizontalAlignment": 0,
        "BTTMenuVerticalAlignment": 0,
        "BTTMenuHorizontalSpacing": 0,
        "BTTMenuVerticalSpacing": 2,
        "BTTMenuVisibility": 1,
        "BTTMenuCloseOnOutsideClick": 1,
        "BTTMenuCloseOnMoveMouseAway": 0,
        "BTTMenuCloseAfterAction": 1,
        "BTTMenuDisableDrag": 1,
        "BTTMenuWindowResizable": 0,
        "BTTMenuWindowLevel": 3,
        "BTTMenuOpacityActive": 1,
        "BTTMenuOpacityInactive": 1,
        "BTTMenuItemVisibleWhileActive": 1,
        "BTTMenuItemVisibleWhileInactive": 1,
        "BTTMenuItemMinWidth": width,
        "BTTMenuItemMaxWidth": width,
        "BTTMenuItemMinHeight": item_height,
        "BTTMenuItemMaxHeight": item_height,
        "BTTMenuItemPaddingLeft": 6,
        "BTTMenuItemPaddingRight": 6,
        "BTTMenuItemPaddingTop": 2,
        "BTTMenuItemPaddingBottom": 2,
        "BTTMenuItemCornerRadius": 8,
        "BTTMenuItemBackgroundType": 4,
        "BTTMenuItemBackgroundColor": "38.000000, 38.000000, 38.000000, 245.000000",
        "BTTMenuItemBackgroundColorDark": "38.000000, 38.000000, 38.000000, 245.000000",
        "BTTMenuItemBackgroundColorHover": "90.000000, 90.000000, 180.000000, 220.000000",
        "BTTMenuItemBackgroundColorHoverDark": "90.000000, 90.000000, 180.000000, 220.000000",
        "BTTMenuAppearanceStyle": 1,
        "BTTMenuAlwaysUseLightMode": 1,
        "BTTMenuElementIdentifier": identifier,
        "BTTLastChangeUUID": new_uuid(),
    }


def normalize_dropdown_items(items: list[dict[str, Any]], root_uuid: str, width: int) -> None:
    for order, item in enumerate(items):
        item["BTTOrder"] = order
        item["BTTTriggerParentUUID"] = root_uuid
        item["BTTEnabled"] = 1
        config = item.setdefault("BTTMenuConfig", {})
        config.update(
            {
                "BTTMenuItemVisibleWhileActive": 1,
                "BTTMenuItemVisibleWhileInactive": 1,
                "BTTMenuItemMinWidth": width,
                "BTTMenuItemMaxWidth": width,
                "BTTMenuItemMinHeight": 40,
                "BTTMenuItemMaxHeight": 40,
            }
        )
        for action_order, action in enumerate(item.get("BTTMenuItemActions", [])):
            action["BTTOrder"] = action_order
            action["BTTTriggerParentUUID"] = item["BTTUUID"]
            action["BTTEnabled"] = 1


def make_dropdown(
    source: dict[str, Any],
    *,
    dropdown_name: str,
    parent_uuid: str,
    width: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = remap_entity_uuids(copy.deepcopy(source))
    now = time.time()

    items = [
        item
        for item in source["BTTMenuItems"]
        if item.get("BTTTriggerType") != 777
    ]
    if not items:
        raise ValueError("submenu contains no items after removing its Back button")

    root_uuid = new_uuid()
    identifier = f"media-dropdown-{dropdown_name.lower().replace(' ', '-')}"
    normalize_dropdown_items(items, root_uuid, width)

    menu = {
        "BTTLastUpdatedAt": now,
        "BTTTriggerType": 767,
        "BTTTriggerTypeDescriptionReadOnly": "Floating Menu",
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTUUID": root_uuid,
        "BTTEnabled": 1,
        "BTTActionCategory": 0,
        "BTTTriggerName": f"Floating Menu: {dropdown_name}",
        "BTTMenuItems": items,
        "BTTMenuConfig": dropdown_menu_config(identifier, len(items), width),
        "BTTMenuAvailability": 0,
        "BTTMenuName": dropdown_name,
        "BTTGestureNotes": "Standalone vertical dropdown converted from a Media submenu",
    }

    button_uuid = new_uuid()
    button = {key: copy.deepcopy(value) for key, value in source.items() if key != "BTTMenuItems"}
    button.update(
        {
            "BTTLastUpdatedAt": now,
            "BTTTriggerType": 773,
            "BTTTriggerTypeDescription": "Standard Item",
            "BTTTriggerTypeDescriptionReadOnly": "Standard Item",
            "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
            "BTTUUID": button_uuid,
            "BTTTriggerParentUUID": parent_uuid,
            "BTTEnabled": 1,
            "BTTActionCategory": 0,
            "BTTTriggerName": f"Menu Item: {source.get('BTTMenuName', dropdown_name)}",
            "BTTGestureNotes": "Shows standalone vertical dropdown",
            "BTTMenuItemActions": [
                {
                    "BTTActionCategory": 0,
                    "BTTLastUpdatedAt": now,
                    "BTTTriggerParentUUID": button_uuid,
                    "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
                    "BTTUUID": new_uuid(),
                    "BTTEnabled": 1,
                    "BTTOrder": 0,
                    "BTTMenuAvailability": 0,
                    "BTTPredefinedActionType": 386,
                    "BTTPredefinedActionName": "Show Floating Menu",
                    "BTTAdditionalActionData": {
                        "BTTMenuActionMenuID": root_uuid,
                        "BTTMenuActionMenuName": dropdown_name,
                        "BTTMenuActionActivateKeyboardFocus": 0,
                        "BTTMenuActionHideOnModifierRelease": 0,
                        "BTTMenuActionRestorePosition": True,
                        "BTTMenuActionTriggerHoveredOnHide": 0,
                        "BTTMenuActionCloseSubmenuOnHide": 0,
                    },
                }
            ],
        }
    )
    button.pop("BTTOrder", None)
    button_config = button.setdefault("BTTMenuConfig", {})
    button_config["BTTMenuElementIdentifier"] = f"{identifier}-button"
    button_config["BTTLastChangeUUID"] = new_uuid()

    return [menu], [button]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="one exported submenu JSON file")
    parser.add_argument("--name", required=True, help="unique standalone menu name")
    parser.add_argument("--output-prefix", type=Path, required=True)
    parser.add_argument("--parent-uuid", default=MEDIA_UUID)
    parser.add_argument("--width", type=int, default=180)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.width < 80:
        raise ValueError("--width must be at least 80 pixels")
    source = load_single_trigger(args.source)
    menu, button = make_dropdown(
        source,
        dropdown_name=args.name,
        parent_uuid=args.parent_uuid,
        width=args.width,
    )

    menu_path = args.output_prefix.with_name(f"{args.output_prefix.name}_menu.json")
    button_path = args.output_prefix.with_name(f"{args.output_prefix.name}_button.json")
    menu_path.parent.mkdir(parents=True, exist_ok=True)
    menu_path.write_text(json.dumps(menu, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    button_path.write_text(json.dumps(button, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(menu_path)
    print(button_path)


if __name__ == "__main__":
    main()
