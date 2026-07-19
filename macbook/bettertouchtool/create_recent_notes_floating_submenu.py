#!/usr/bin/env python3
"""Generate the import-safe shell for a dynamic Apple Notes submenu.

BTT 6.011 crashes its configuration UI when BTTMenuScriptSettings are imported
on a submenu. The content script is therefore installed manually after this
otherwise ordinary submenu has been imported.
"""

import json
import sys
import time
import uuid

from btt_touchbar_folder_to_floating_submenu import (
    back_button_item,
    base_menu_config,
)


DEFAULT_PARENT_UUID = "D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
DEFAULT_LIMIT = 10
def new_uuid():
    return str(uuid.uuid4()).upper()


def build_submenu(parent_uuid, limit):
    submenu_uuid = new_uuid()
    config = base_menu_config("Recent Notes", label_text="Notes")

    return {
        "BTTActionCategory": 0,
        "BTTLastUpdatedAt": time.time(),
        "BTTTriggerType": 774,
        "BTTTriggerTypeDescriptionReadOnly": "Sub Menu",
        "BTTTriggerTypeDescription": "Standard Item",
        "BTTTriggerParentUUID": parent_uuid,
        "BTTTriggerClass": "BTTTriggerTypeFloatingMenu",
        "BTTUUID": submenu_uuid,
        "BTTEnabled": 1,
        "BTTMenuItems": [back_button_item(submenu_uuid, order=0)],
        "BTTMenuConfig": config,
        "BTTMenuAvailability": 0,
        "BTTMenuName": "Recent Notes",
        "BTTGestureNotes": (
            f"Apple Notes submenu shell; configure its content script for {limit} notes"
        ),
    }


def main():
    parent_uuid = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PARENT_UUID
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LIMIT
    if not 1 <= limit <= 30:
        raise SystemExit("note limit must be between 1 and 30")

    print(json.dumps([build_submenu(parent_uuid, limit)], indent=2))


if __name__ == "__main__":
    main()
