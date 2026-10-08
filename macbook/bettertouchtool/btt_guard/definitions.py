"""Build restorable flat BTT definitions from read-only runtime exports."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
import math
from typing import Any

from .database import OPERATIONAL_CONFIG_KEYS
from .model import ConfigSnapshot, SCHEMA_VERSION


COLLECTIONS = (
    "BTTMenuItems",
    "BTTMenuItemActions",
    "BTTAdditionalActions",
    "BTTMenuItemTriggers",
)
VOLATILE_KEYS = {
    "BTTLastUpdatedAt",
    "BTTLastChangeUUID",
    "BTTFloatingMenuRenderedPreview",
    "BTTTriggerTypeDescriptionReadOnly",
    "BTTIsPureAction",
}
FLOATING_TYPES = {767, 773, 774, 777, 801}
BOOLEAN_CONFIG_KEYS = {
    "BTTMenuCloseAfterAction",
    "BTTMenuCloseOnOutsideClick",
    "BTTMenuDisableDrag",
    "BTTMenuItemCloseOnClick",
    "BTTMenuItemScriptActive",
    "BTTMenuItemScriptRunWhileMenuIsHidden",
    "BTTMenuItemVisibleWhileActive",
    "BTTMenuItemVisibleWhileInactive",
    "BTTMenuItemsUseModifierModes",
    "BTTMenuMergeGlobalMenuItems",
    "BTTMenuPositionPreventOffscreen",
    "BTTMenuScriptAlwaysRunOnAppear",
    "BTTMenuScriptAlwaysRunOnFirstLoad",
    "BTTMenuScriptRunOnItemHover",
    "BTTMenuScriptRunOnMenuHover",
    "BTTMenuShowIfWindowLevelEqualsEnabled",
}
PAYLOAD_FIELDS = {
    "BTTShellTaskActionScript",
    "BTTTerminalCommand",
    "BTTNamedTriggerToTrigger",
    "BTTLayoutIndependentActionChar",
    "BTTDelayNextActionBy",
    "BTTAdditionalActionData",
    "BTTShortcutToSend",
    "BTTShellTaskActionConfig",
}
LAUNCH_PATH_FIELDS = {
    206: "BTTShellTaskActionScript",
    246: "BTTTerminalCommand",
    248: "BTTNamedTriggerToTrigger",
    -1: "BTTLayoutIndependentActionChar",
    264: "BTTLayoutIndependentActionChar",
    129: "BTTDelayNextActionBy",
}
TOP_LEVEL_TEXT_FIELDS = {
    "BTTMenuName",
    "BTTTriggerName",
    "BTTName",
    "BTTTouchBarButtonName",
}
MENU_TEXT_FIELDS = {
    "BTTMenuItemText",
    "BTTMenuAttributedText",
    "BTTMenuText",
}


def _as_int(value: object, field: str, uuid: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Runtime {field} is invalid for BTT record {uuid}.")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Runtime {field} is invalid for BTT record {uuid}.") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"Runtime {field} is invalid for BTT record {uuid}.")
    if isinstance(value, str) and value.strip() != str(number):
        raise ValueError(f"Runtime {field} is invalid for BTT record {uuid}.")
    return number


def _flatten(
    exports: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]], dict[str, str | None], dict[str, str], set[str]
]:
    nodes: dict[str, dict[str, Any]] = {}
    parents: dict[str, str | None] = {}
    roles: dict[str, str] = {}
    top_ids: set[str] = set()

    def visit(item: object, parent: str | None, role: str) -> None:
        if not isinstance(item, dict):
            raise ValueError("BTT runtime export contains a non-object definition.")
        uuid = item.get("BTTUUID")
        if not isinstance(uuid, str) or not uuid:
            raise ValueError("BTT runtime export contains a definition without a UUID.")
        if uuid in nodes:
            raise ValueError(f"Duplicate runtime BTT UUID: {uuid}")
        node = deepcopy(item)
        for key in COLLECTIONS:
            children = node.pop(key, [])
            if not isinstance(children, list):
                raise ValueError(f"Runtime collection {key} is invalid for BTT record {uuid}.")
            child_role = {
                "BTTMenuItems": "item",
                "BTTMenuItemActions": "action",
                "BTTAdditionalActions": "action",
                "BTTMenuItemTriggers": "trigger",
            }[key]
            for child in children:
                visit(child, uuid, child_role)
        for key in list(node):
            if key in VOLATILE_KEYS or key.endswith("ReadOnly"):
                node.pop(key)
        nodes[uuid] = node
        parents[uuid] = parent
        roles[uuid] = role

    if not isinstance(exports, list):
        raise ValueError("BTT runtime exports must be a list.")
    for exported in exports:
        if not isinstance(exported, dict):
            raise ValueError("BTT runtime export contains a non-object root.")
        uuid = exported.get("BTTUUID")
        visit(exported, None, "root")
        if isinstance(uuid, str):
            top_ids.add(uuid)
    return nodes, parents, roles, top_ids


def _fill_compact_action_types(
    nodes: dict[str, dict[str, Any]],
    roles: dict[str, str],
    records: dict[str, dict[str, Any]],
) -> None:
    for uuid, expected in records.items():
        action = int(expected["trigger_type"]) == -1
        if roles[uuid] == "action":
            if not action:
                raise ValueError(f"Runtime action collection role differs for BTT record {uuid}.")
            nodes[uuid].setdefault("BTTTriggerType", -1)
        elif action:
            raise ValueError(f"Runtime action has an unexpected collection role: {uuid}")


def _same_json(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def _boolean_value(value: object, key: str, uuid: str) -> bool:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    raise ValueError(f"Runtime operational configuration differs at {key}: {uuid}")


def _validate_config(expected: dict[str, Any], node: dict[str, Any], uuid: str) -> None:
    actual = node.get("BTTMenuConfig", {})
    if not isinstance(actual, dict):
        raise ValueError(f"Runtime menu configuration is invalid for BTT record {uuid}.")
    for key in OPERATIONAL_CONFIG_KEYS:
        expected_present = key in expected
        actual_present = key in actual
        if key in BOOLEAN_CONFIG_KEYS:
            wanted = _boolean_value(expected[key], key, uuid) if expected_present else False
            found = _boolean_value(actual[key], key, uuid) if actual_present else False
            if wanted != found:
                raise ValueError(f"Runtime operational configuration differs at {key}: {uuid}")
        elif expected_present != actual_present or (
            expected_present and not _same_json(expected[key], actual[key])
        ):
            raise ValueError(f"Runtime operational configuration differs at {key}: {uuid}")


def _runtime_action_type(node: dict[str, Any], uuid: str) -> int | None:
    if "BTTPredefinedActionType" not in node:
        return None
    return _as_int(node["BTTPredefinedActionType"], "action type", uuid)


def _keyboard_payload(payload: dict[str, Any]) -> bool:
    shortcut = payload.get("shortcut")
    return "launch_path" in payload or shortcut not in (None, "", "-1", -1)


def _validate_action_type(expected: dict[str, Any], node: dict[str, Any], uuid: str) -> None:
    wanted = int(expected["action_type"])
    actual = _runtime_action_type(node, uuid)
    if wanted == -1 and _keyboard_payload(expected["payload"]):
        valid = actual == 264
    elif wanted in (-1, 366):
        valid = actual in (None, -1, 366)
    else:
        valid = actual == wanted
    if not valid:
        raise ValueError(f"Runtime action type differs for BTT record {uuid}.")


def _numeric_equal(left: object, right: object) -> bool:
    try:
        first, second = float(left), float(right)
    except (TypeError, ValueError):
        return False
    return math.isfinite(first) and math.isfinite(second) and first == second


def _decoded_action_data(value: object, uuid: str) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Runtime action data is invalid for BTT record {uuid}.") from exc
    return value


def _payload_field(key: str, expected: dict[str, Any], uuid: str) -> str:
    action_type = int(expected["action_type"])
    if key == "launch_path":
        try:
            return LAUNCH_PATH_FIELDS[action_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported launch-path action mapping for BTT record {uuid}.") from exc
    if key == "action_data":
        return "BTTAdditionalActionData"
    if key == "shortcut":
        return "BTTShortcutToSend"
    if key == "gesture_config" and int(expected["trigger_type"]) == 643:
        return "BTTTriggerName"
    if key == "additional_action_string" and action_type == 206:
        return "BTTShellTaskActionConfig"
    raise ValueError(f"Unsupported payload mapping {key} for BTT record {uuid}.")


def _validate_payload(expected: dict[str, Any], node: dict[str, Any], uuid: str) -> None:
    claimed: set[str] = set()
    for key, wanted in expected["payload"].items():
        field = _payload_field(key, expected, uuid)
        claimed.add(field)
        if key == "shortcut" and wanted in (None, "", "-1", -1):
            if field not in node or node[field] in (None, "", "-1", -1):
                continue
        if field not in node:
            raise ValueError(f"Runtime payload is missing {field}: {uuid}")
        actual = node[field]
        if key == "action_data":
            actual = _decoded_action_data(actual, uuid)
        equal = _numeric_equal(wanted, actual) if field == "BTTDelayNextActionBy" else _same_json(wanted, actual)
        if not equal:
            raise ValueError(f"Runtime payload differs at {field}: {uuid}")
    functional_fields = set(PAYLOAD_FIELDS)
    if int(expected["trigger_type"]) == 643:
        functional_fields.add("BTTTriggerName")
    for field in functional_fields - claimed:
        if field in node and node[field] not in (None, "", "-1", -1):
            raise ValueError(f"Runtime payload has unexpected {field}: {uuid}")


def _validate_record(
    uuid: str,
    expected: dict[str, Any],
    node: dict[str, Any],
    observed_parent: str | None,
) -> None:
    wanted_parent = expected["parent"]
    declared_parent = node.get("BTTTriggerParentUUID")
    if observed_parent != wanted_parent or (
        declared_parent is not None and declared_parent != wanted_parent
    ):
        raise ValueError(f"Runtime parent differs for BTT record {uuid}.")
    trigger_type = _as_int(node.get("BTTTriggerType"), "trigger type", uuid)
    if trigger_type != int(expected["trigger_type"]):
        raise ValueError(f"Runtime trigger type differs for BTT record {uuid}.")
    enabled = _as_int(node.get("BTTEnabled", 1), "enabled state", uuid) != 0
    if enabled != bool(expected["enabled"]):
        raise ValueError(f"Runtime enabled state differs for BTT record {uuid}.")
    category = _as_int(node.get("BTTActionCategory", 0), "action category", uuid)
    if category != int(expected["action_category"]):
        raise ValueError(f"Runtime action category differs for BTT record {uuid}.")
    _validate_action_type(expected, node, uuid)
    _validate_payload(expected, node, uuid)
    _validate_config(expected["config"], node, uuid)


def _expected_app_scope(expected: dict[str, Any], uuid: str) -> str | None:
    scopes = expected.get("app_scope")
    if (
        not isinstance(scopes, list)
        or any(not isinstance(scope, str) or not scope for scope in scopes)
        or len(set(scopes)) != len(scopes)
    ):
        raise ValueError(f"Saved application scope is invalid for BTT record {uuid}.")
    if len(scopes) > 1:
        raise ValueError(f"Multiple application scopes cannot be preserved for BTT record {uuid}.")
    if not scopes:
        top_level_named = (
            expected.get("parent") is None and int(expected.get("trigger_type", -999)) == 643
        )
        if not top_level_named:
            raise ValueError(f"BTT record has no preservable application scope: {uuid}")
        return None
    return scopes[0]


def _validate_app_scope(
    expected: dict[str, Any],
    node: dict[str, Any],
    uuid: str,
    *,
    require_explicit: bool,
) -> None:
    wanted = _expected_app_scope(expected, uuid)
    actual = node.get("BTTAppBundleIdentifier")
    if wanted is None:
        if actual not in (None, ""):
            raise ValueError(f"Runtime application scope differs for BTT record {uuid}.")
        return
    if actual is not None and actual != wanted:
        raise ValueError(f"Runtime application scope differs for BTT record {uuid}.")
    if require_explicit and actual != wanted:
        raise ValueError(f"Definition application scope is missing for BTT record {uuid}.")


def _canonicalize_orders(
    nodes: dict[str, dict[str, Any]], records: dict[str, dict[str, Any]]
) -> None:
    groups: dict[tuple[str | None, int], list[str]] = defaultdict(list)
    for uuid, expected in records.items():
        if int(expected["trigger_type"]) == -1:
            groups[(expected["parent"], int(expected["action_category"]))].append(uuid)
            continue
        order = _as_int(nodes[uuid].get("BTTOrder", 0), "order", uuid)
        if order != int(expected["order"]):
            raise ValueError(f"Runtime order differs for BTT record {uuid}.")
        nodes[uuid]["BTTOrder"] = order
    for siblings in groups.values():
        ordered = sorted(
            siblings,
            key=lambda uuid: (
                _as_int(nodes[uuid].get("BTTOrder", 0), "order", uuid),
                uuid,
            ),
        )
        for rank, uuid in enumerate(ordered):
            if rank != int(records[uuid]["order"]):
                raise ValueError(f"Runtime action order differs for BTT record {uuid}.")
            nodes[uuid]["BTTOrder"] = rank


def _assign_classes(
    nodes: dict[str, dict[str, Any]],
    records: dict[str, dict[str, Any]],
    *,
    require_explicit: bool = False,
) -> None:
    visiting: set[str] = set()

    def assign(uuid: str) -> str:
        existing = nodes[uuid].get("BTTTriggerClass")
        if existing is not None and (not isinstance(existing, str) or not existing):
            raise ValueError(f"Runtime trigger class is invalid for BTT record {uuid}.")
        if uuid in visiting:
            raise ValueError("BTT definition parents contain a cycle.")
        visiting.add(uuid)
        trigger_type = int(records[uuid]["trigger_type"])
        if trigger_type == -1:
            parent = records[uuid]["parent"]
            if not isinstance(parent, str) or parent not in nodes:
                raise ValueError(f"Cannot derive trigger class for BTT action {uuid}.")
            trigger_class = assign(parent)
        elif trigger_type in FLOATING_TYPES:
            trigger_class = "BTTTriggerTypeFloatingMenu"
        elif trigger_type == 643:
            trigger_class = "BTTTriggerTypeOtherTriggers"
        else:
            if not isinstance(existing, str) or not existing:
                raise ValueError(f"Cannot derive trigger class for unfamiliar BTT type: {uuid}")
            trigger_class = existing
        if existing is not None and existing != trigger_class:
            raise ValueError(f"Runtime trigger class differs for BTT record {uuid}.")
        if require_explicit and existing is None:
            raise ValueError(f"Definition trigger class is missing for BTT record {uuid}.")
        visiting.remove(uuid)
        nodes[uuid]["BTTTriggerClass"] = trigger_class
        return trigger_class

    for uuid in records:
        assign(uuid)


def _finalize(
    nodes: dict[str, dict[str, Any]],
    records: dict[str, dict[str, Any]],
    preset_names: dict[str, str],
) -> None:
    for uuid, expected in records.items():
        node = nodes[uuid]
        node["BTTTriggerType"] = int(expected["trigger_type"])
        if int(expected["trigger_type"]) == -1:
            node["BTTActionCategory"] = int(expected["action_category"])
        parent = expected["parent"]
        if parent is None:
            node.pop("BTTTriggerParentUUID", None)
            node.setdefault("BTTAppBundleIdentifier", "BT.G")
        else:
            node["BTTTriggerParentUUID"] = parent
        preset = expected["preset"]
        if preset is None:
            node.pop("BTTTriggerBelongsToPreset", None)
        else:
            name = preset_names.get(preset)
            if not isinstance(name, str) or not name:
                raise ValueError(f"Missing current preset name for BTT record {uuid}.")
            node["BTTTriggerBelongsToPreset"] = name
        scope = _expected_app_scope(expected, uuid)
        if scope is None:
            node.pop("BTTAppBundleIdentifier", None)
        else:
            node["BTTAppBundleIdentifier"] = scope
    _assign_classes(nodes, records)


def _validate_preset(expected: dict[str, Any], node: dict[str, Any], uuid: str) -> None:
    preset = expected.get("preset")
    name = node.get("BTTTriggerBelongsToPreset")
    if preset is None:
        if name is not None:
            raise ValueError(f"Definition preset is unexpected for BTT record {uuid}.")
    elif not isinstance(name, str) or not name:
        raise ValueError(f"Definition preset name is missing for BTT record {uuid}.")


def validate_definitions(
    definitions: object,
    snapshot: ConfigSnapshot,
) -> None:
    """Reject a flat checkpoint definition set that disagrees with its snapshot."""
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported BTT snapshot schema for definitions.")
    records = snapshot.get("records")
    if not isinstance(records, dict) or not records or not isinstance(definitions, dict):
        raise ValueError("Checkpoint definitions have an invalid graph shape.")
    if set(definitions) != set(records):
        raise ValueError("Checkpoint definition UUIDs do not match the saved graph.")
    checked: dict[str, dict[str, Any]] = {}
    for uuid, expected in records.items():
        node = definitions.get(uuid)
        if not isinstance(uuid, str) or not isinstance(expected, dict) or not isinstance(node, dict):
            raise ValueError("Checkpoint definitions contain an invalid record.")
        if node.get("BTTUUID") != uuid:
            raise ValueError(f"Definition UUID differs from its checkpoint key: {uuid}")
        if any(key in node for key in COLLECTIONS):
            raise ValueError(f"Checkpoint definition is not flat: {uuid}")
        _validate_record(uuid, expected, node, expected.get("parent"))
        order = _as_int(node.get("BTTOrder", 0), "order", uuid)
        if order != int(expected["order"]):
            raise ValueError(f"Definition order differs for BTT record {uuid}.")
        _validate_preset(expected, node, uuid)
        _validate_app_scope(expected, node, uuid, require_explicit=True)
        checked[uuid] = deepcopy(node)
    _assign_classes(checked, records, require_explicit=True)


def _meaningful(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value is not False and value != 0 and bool(value)


def _has_text(
    node: dict[str, Any], fields: set[str], *, in_config: bool = False
) -> bool:
    source = node.get("BTTMenuConfig", {}) if in_config else node
    return isinstance(source, dict) and any(_meaningful(source.get(key)) for key in fields)


def _has_icon(node: dict[str, Any], suffix: str) -> bool:
    config = node.get("BTTMenuConfig", {})
    if not isinstance(config, dict):
        return False
    return any(
        _meaningful(config.get(key + suffix))
        for key in ("BTTMenuItemImage", "BTTMenuItemIconPresetPath", "BTTMenuItemIcon")
    )


def verify_created_appearance(
    definitions: object,
    runtime_definitions: object,
    created_ids: object,
) -> None:
    """Verify created records retained meaningful text and light/dark icon presence."""
    if not isinstance(definitions, dict) or not isinstance(runtime_definitions, dict):
        raise ValueError("Appearance verification requires flat definition mappings.")
    if isinstance(created_ids, (str, bytes)):
        raise ValueError("Created definition IDs must be an iterable of IDs.")
    try:
        ids = list(created_ids)
    except TypeError as exc:
        raise ValueError("Created definition IDs must be iterable.") from exc
    if any(not isinstance(uuid, str) or not uuid for uuid in ids) or len(set(ids)) != len(ids):
        raise ValueError("Created definition IDs are invalid or duplicated.")
    for uuid in ids:
        expected = definitions.get(uuid)
        actual = runtime_definitions.get(uuid)
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise ValueError(f"Created BTT definition is unavailable for appearance check: {uuid}")
        if expected.get("BTTUUID") != uuid or actual.get("BTTUUID") != uuid:
            raise ValueError(f"Created BTT definition has the wrong UUID: {uuid}")
        for fields, label, in_config in (
            (TOP_LEVEL_TEXT_FIELDS, "name", False),
            (MENU_TEXT_FIELDS, "menu text", True),
        ):
            if _has_text(expected, fields, in_config=in_config) and not _has_text(
                actual, fields, in_config=in_config
            ):
                raise ValueError(f"Created BTT definition lost meaningful {label}: {uuid}")
        for suffix, label in (("", "icon"), ("Dark", "dark icon")):
            if _has_icon(expected, suffix) and not _has_icon(actual, suffix):
                raise ValueError(f"Created BTT definition lost its {label}: {uuid}")


def build_definitions(
    exports: list[dict[str, Any]],
    snapshot: ConfigSnapshot,
    preset_names: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Validate runtime exports against SQLite state and flatten them for repair."""
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported BTT snapshot schema for definition capture.")
    records = snapshot.get("records")
    if not isinstance(records, dict) or not records:
        raise ValueError("BTT snapshot contains no records for definition capture.")
    nodes, observed_parents, roles, top_ids = _flatten(exports)
    expected_ids = set(records)
    if set(nodes) != expected_ids:
        missing = sorted(expected_ids - set(nodes))
        unexpected = sorted(set(nodes) - expected_ids)
        category = "missing" if missing else "unexpected"
        uuid = (missing or unexpected)[0]
        raise ValueError(f"BTT runtime export has {category} UUID: {uuid}")
    _fill_compact_action_types(nodes, roles, records)
    for uuid in top_ids:
        expected = records[uuid]
        if expected["parent"] is not None or int(expected["trigger_type"]) not in (643, 767):
            raise ValueError(f"Unexpected top-level BTT export: {uuid}")
    for uuid, expected in records.items():
        _validate_app_scope(expected, nodes[uuid], uuid, require_explicit=False)
        _validate_record(uuid, expected, nodes[uuid], observed_parents[uuid])
    _canonicalize_orders(nodes, records)
    _finalize(nodes, records, preset_names)
    result = {uuid: nodes[uuid] for uuid in records}
    validate_definitions(result, snapshot)
    return result
