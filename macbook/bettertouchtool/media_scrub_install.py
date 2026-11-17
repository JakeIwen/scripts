"""Plan and guarded transaction for the Media track-scrub slider (install_media_scrub.py).

One transaction changes PPause (script and update interval) and adds or updates the
slider's Web View item directly after it. Every field it may write is recorded before the
write as an Edit (old value, intended value). A failure restores each field only while it
still holds the intended value, so a concurrent edit is kept and reported rather than
overwritten; anything unrestored is reported as ROLLBACK INCOMPLETE.

Works through BTT's socket CLI (`call(command, **parameters)`, btt_socket.call), which
tests replace with an in-memory fake. Python 3.9+, stdlib only.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from btt_common import MEDIA
from media_submenu import ITEM

PPAUSE = "BDFEAEF1-6961-4B05-9A62-E70C54332C4C"
NAMESPACE = uuid.UUID("8E2A6B1C-3D4F-4A57-9C80-1B7E5D2F6A93")
SCRUB = str(uuid.uuid5(NAMESPACE, "media-scrub-slider")).upper()
IDENTIFIER = "media-scrub"
WIDTH, HEIGHT = 220, 40  # the Media bar's 40 px row
PLACEHOLDER = '"__MEDIA_SCRUB_PATH__"'
UPDATE_INTERVAL = 1
# Fields BTT leaves out of an export while they hold their default.
DEFAULTS = {"BTTMenuItemKeepActiveInBackground": 0}
VOLATILE = {"BTTLastUpdatedAt", "BTTLastChangeUUID", "BTTFloatingMenuRenderedPreview"}
ABSENT = {"__absent__": True}
SAVED_WAIT_SECONDS = 45

Call = Callable[..., str]
Edit = Dict[str, Any]


class Refusal(RuntimeError):
    """The menu differs from the inspected shape; nothing was written."""


class InstallFailed(RuntimeError):
    def __init__(self, message: str, incomplete: bool, backup: Any):
        super().__init__(message)
        self.incomplete, self.backup = incomplete, backup


def canon(value: Any) -> Any:
    """A comparison form: flags and integral floats are plain numbers, volatile keys are gone."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: canon(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        items = [canon(item) for item in value]
        if items and all(isinstance(item, dict) and "BTTUUID" in item for item in items):
            items.sort(key=lambda item: item["BTTUUID"])
        return items
    return value


def same(left: Any, right: Any) -> bool:
    return canon(left) == canon(right)


def read_trigger(call: Call, uid: str) -> Any:
    value = json.loads(call("get_trigger", uuid=uid) or "{}")
    hits = [item for item in (value if isinstance(value, list) else [value])
            if isinstance(item, dict) and item.get("BTTUUID") == uid]
    if len(hits) > 1:
        raise Refusal("BTT exported more than one trigger %s; no changes made." % uid)
    return hits[0] if hits else None


def render_play_status(template: str, script_path: str) -> str:
    """PPause's content script with the absolute media_scrub.py path substituted in."""
    if PLACEHOLDER not in template:
        raise ValueError("play_status.js lacks its path placeholder")
    if not script_path.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in script_path):
        raise ValueError("Invalid media_scrub.py path")
    return template.replace(PLACEHOLDER, json.dumps(script_path))


def scrub_config(page: str) -> Dict[str, Any]:
    # BTTMenuItemWebViewPlain 0 keeps BTT's script bridge (runShellScript) in the page, and
    # KeepActiveInBackground 0 lets BTT suspend the page when the bar closes.
    return {"BTTMenuElementIdentifier": IDENTIFIER, "BTTMenuItemText": page,
            "BTTMenuItemWebViewPlain": 0, "BTTMenuItemKeepActiveInBackground": 0,
            "BTTMenuItemMinWidth": WIDTH, "BTTMenuItemMaxWidth": WIDTH,
            "BTTMenuItemMinHeight": HEIGHT, "BTTMenuItemMaxHeight": HEIGHT,
            "BTTMenuItemBackgroundType": 4, "BTTMenuAlwaysUseLightMode": 1,
            "BTTMenuItemVisibleWhileActive": 1, "BTTMenuItemVisibleWhileInactive": 1}


def scrub_item(page: str, order: int) -> Dict[str, Any]:
    return dict(ITEM, BTTUUID=SCRUB, BTTTriggerType=778, BTTTriggerParentUUID=MEDIA, BTTOrder=order,
                BTTMenuName=IDENTIFIER, BTTMenuConfig=scrub_config(page))


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _enabled(node: Dict[str, Any]) -> bool:
    return all(int(node.get(key, 1)) == 1 for key in ("BTTEnabled", "BTTEnabled2"))


def _order(node: Dict[str, Any]) -> int:
    value = node.get("BTTOrder")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise Refusal("Media item %s has a missing or fractional order; no changes made." % node.get("BTTUUID"))
    return int(value)


def config_value(node: Dict[str, Any], key: str) -> Any:
    config = node.get("BTTMenuConfig") or {}
    return config[key] if key in config else DEFAULTS.get(key, ABSENT)


def _config_edits(node: Dict[str, Any], uid: str, wanted: Dict[str, Any]) -> List[Edit]:
    return [dict(uuid=uid, scope="config", key=key, old=config_value(node, key), new=value)
            for key, value in wanted.items() if not same(config_value(node, key), value)]


def _check_media(media: Any) -> None:
    config = (media or {}).get("BTTMenuConfig") or {}
    if (not media or media.get("BTTUUID") != MEDIA or _int(media.get("BTTTriggerType")) != 767
            or not _enabled(media) or not isinstance(media.get("BTTMenuItems"), list)
            or config.get("BTTMenuElementIdentifier") != "Media"):
        raise Refusal("Cannot read the complete enabled Media menu; no changes made.")


def _check_ppause(ppause: Dict[str, Any]) -> None:
    settings = (ppause.get("BTTMenuConfig") or {}).get("BTTMenuScriptSettings") or {}
    script = str(settings.get("BTTAppleScriptString", ""))
    # The old ssh script (play_status) and a previous run of this installer are both valid priors.
    if (_int(ppause.get("BTTTriggerType")) != 773 or not _enabled(ppause)
            or config_value(ppause, "BTTMenuElementIdentifier") != "PPause"
            or _int(config_value(ppause, "BTTMenuItemScriptActive")) != 1
            or _int(settings.get("BTTScriptType")) != 3
            or not ("play_status" in script or "media_scrub.py" in script)):
        raise Refusal("PPause differs from the inspected shape; no changes made.")


def make_plan(media: Any, scrub: Any, play_source: str, page: str) -> Dict[str, Any]:
    """Every intended change, read-only. Raises Refusal when Media or PPause differ from the known shape."""
    _check_media(media)
    children = media["BTTMenuItems"]
    old_orders = {child["BTTUUID"]: _order(child) for child in children}
    hits = [child for child in children if child["BTTUUID"] == PPAUSE]
    if len(hits) != 1 or len(old_orders) != len(children):
        raise Refusal("PPause is missing, duplicated or the menu has duplicate UUIDs; no changes made.")
    ppause = hits[0]
    _check_ppause(ppause)
    settings = dict(ppause["BTTMenuConfig"]["BTTMenuScriptSettings"],
                    BTTAppleScriptString=play_source, BTTScriptFunctionToCall="itemScript")
    edits = _config_edits(ppause, PPAUSE, {"BTTMenuScriptSettings": settings,
                                           "BTTMenuScriptUpdateInterval": UPDATE_INTERVAL})
    existing = next((child for child in children if child["BTTUUID"] == SCRUB), None)
    if existing is None and scrub is not None:
        raise Refusal("The slider's UUID exists outside the Media bar; no changes made.")
    shifts: Dict[str, tuple] = {}
    if existing is not None:
        if _int(existing.get("BTTTriggerType")) != 778 or not _enabled(existing):
            raise Refusal("The slider item is disabled or has another type; fix it in BTT. No changes made.")
        # An installed item keeps its position, so a hand reorder survives.
        order = old_orders[SCRUB]
        edits += _config_edits(existing, SCRUB, scrub_config(page))
    else:
        order = old_orders[PPAUSE] + 1
        shifts = {uid: (old, old + 1) for uid, old in old_orders.items() if old > old_orders[PPAUSE]}
        edits += [dict(uuid=uid, scope="order", key="BTTOrder", old=old, new=new)
                  for uid, (old, new) in shifts.items()]
    final = {uid: shifts[uid][1] if uid in shifts else old for uid, old in old_orders.items()}
    final[SCRUB] = order
    names = {child["BTTUUID"]: child.get("BTTMenuName") or "" for child in children}
    return {"media_before": media, "page": page, "play_source": play_source, "exists": existing is not None,
            "edits": edits, "order": order, "final_orders": final, "names": names,
            "settings": settings, "touched": sorted({PPAUSE, SCRUB, *shifts})}


def is_noop(plan: Dict[str, Any]) -> bool:
    return plan["exists"] and not plan["edits"]


def describe(plan: Dict[str, Any]) -> List[str]:
    lines = []
    ppause = [e for e in plan["edits"] if e["uuid"] == PPAUSE]
    lines.append("PPause (%s): %s" % (PPAUSE, "; ".join(
        "%s -> %s" % (e["key"], "media_scrub.py status --text --wait 2" if e["key"] == "BTTMenuScriptSettings"
                      else e["new"]) for e in ppause) or "already configured"))
    if plan["exists"]:
        keys = [e["key"] for e in plan["edits"] if e["uuid"] == SCRUB]
        lines.append("Slider item (%s): update in place (%s), order stays %d" % (
            SCRUB, ", ".join(keys) or "already configured", plan["order"]))
    else:
        lines.append("Slider item (%s): add Web View (type 778) %dx%d after PPause at order %d"
                     % (SCRUB, WIDTH, HEIGHT, plan["order"]))
    shifts = [e for e in plan["edits"] if e["scope"] == "order"]
    for edit in shifts:
        lines.append("  order %s: %s %d -> %d" % (edit["uuid"], plan["names"][edit["uuid"]] or "(unnamed)",
                                                  edit["old"], edit["new"]))
    if not shifts:
        lines.append("  no sibling orders change")
    return lines


# --- apply -----------------------------------------------------------------------------


def _strip(media: Dict[str, Any]) -> Dict[str, Any]:
    """The parts of Media this installer does not own (orders and PPause's two fields are
    checked separately; the slider item is excluded)."""
    result = copy.deepcopy(media)
    result.pop("BTTOrder", None)
    children = [child for child in result["BTTMenuItems"] if child["BTTUUID"] != SCRUB]
    for child in children:
        child.pop("BTTOrder", None)
        if child["BTTUUID"] == PPAUSE:
            for key in ("BTTMenuScriptSettings", "BTTMenuScriptUpdateInterval"):
                (child.get("BTTMenuConfig") or {}).pop(key, None)
    result["BTTMenuItems"] = children
    return result


def _write(call: Call, edits: List[Edit], value_key: str) -> None:
    """Write one value_key ('new' or 'old') of each edit; config edits of one item go in one call."""
    patches: Dict[str, Dict[str, Any]] = {}
    for edit in edits:
        if edit["scope"] == "config":
            patches.setdefault(edit["uuid"], {})[edit["key"]] = edit[value_key]
        else:
            call("update_trigger", uuid=edit["uuid"], json=json.dumps({"BTTOrder": edit[value_key]}))
    for uid, patch in patches.items():
        call("update_menu_item", uuid=uid, json=json.dumps(patch), persist="true")


def _value(call: Call, edit: Edit) -> Any:
    node = read_trigger(call, edit["uuid"])
    if node is None:
        raise RuntimeError("%s disappeared" % edit["uuid"])
    return node.get("BTTOrder") if edit["scope"] == "order" else config_value(node, edit["key"])


def verify(call: Call, plan: Dict[str, Any]) -> None:
    media = read_trigger(call, MEDIA)
    _check_media(media)
    errors = []
    children = {child["BTTUUID"]: child for child in media["BTTMenuItems"]}
    actual = {uid: child.get("BTTOrder") for uid, child in children.items()}
    if not same(actual, plan["final_orders"]):
        errors.append("sibling orders differ from the plan")
    item = children.get(SCRUB)
    if item is None or _int(item.get("BTTTriggerType")) != 778 or not _enabled(item):
        errors.append("slider item missing, disabled or retyped")
    else:
        errors += ["slider %s" % key for key, value in scrub_config(plan["page"]).items()
                   if not same(config_value(item, key), value)]
    ppause = children.get(PPAUSE)
    if ppause is None:
        errors.append("PPause missing")
    else:
        if not same(config_value(ppause, "BTTMenuScriptUpdateInterval"), UPDATE_INTERVAL):
            errors.append("PPause update interval")
        if not same(config_value(ppause, "BTTMenuScriptSettings"), plan["settings"]):
            errors.append("PPause script")
    if not same(_strip(media), _strip(plan["media_before"])):
        errors.append("unexpected change outside the installer's items")
    if errors:
        raise RuntimeError("Readback failed: " + "; ".join(errors))


def saved_errors(saved: Dict[str, Dict[str, Any]], plan: Dict[str, Any]) -> List[str]:
    """Disk state after BTT's asynchronous save, from btt_common.records()."""
    errors = []
    for uid, order in plan["final_orders"].items():
        record = saved.get(uid)
        if record is None:
            errors.append(uid + ": not saved")
        elif record["parent"] != MEDIA or record["ZORDER"] != order:
            errors.append(uid + ": saved parent/order differs")
    record = saved.get(SCRUB)
    if record is not None and (record["ZGESTURETYPE"] != 778 or record["ZENABLEDNEW"] == 0 or any(
            not same(record["config"].get(key, DEFAULTS.get(key, ABSENT)), value)
            for key, value in scrub_config(plan["page"]).items())):
        errors.append(SCRUB + ": saved item differs")
    record = saved.get(PPAUSE)
    if record is not None and (not same(record["config"].get("BTTMenuScriptUpdateInterval"), UPDATE_INTERVAL)
                               or not same(record["config"].get("BTTMenuScriptSettings"), plan["settings"])):
        errors.append(PPAUSE + ": saved script settings differ")
    return errors


def wait_saved(check: Callable[[], List[str]], sleep: Callable[[float], None] = time.sleep,
               monotonic: Callable[[], float] = time.monotonic, seconds: float = SAVED_WAIT_SECONDS) -> None:
    deadline = monotonic() + seconds
    while True:
        errors = check()
        if not errors:
            return
        if monotonic() >= deadline:
            raise RuntimeError("BTT has not saved the slider: " + "; ".join(errors))
        sleep(0.5)


def _restore(call: Call, edit: Edit, problems: List[str]) -> None:
    label = "%s %s" % (edit["uuid"], edit["key"])
    try:
        current = _value(call, edit)
        if same(current, edit["old"]):
            return
        if not same(current, edit["new"]):
            problems.append("%s was changed by someone else; left as it is" % label)
            return
        if same(edit["old"], ABSENT):
            problems.append("%s cannot be removed; it was added by this attempt" % label)
            return
        _write(call, [edit], "old")
        if not same(_value(call, edit), edit["old"]):
            problems.append("%s did not take its old value" % label)
    except Exception as error:  # every field gets its turn
        problems.append("%s: %s" % (label, error))


def rollback(call: Call, plan: Dict[str, Any], edits: List[Edit], created: bool) -> List[str]:
    """Undo one attempt; returns the problems that remain (empty: every field is restored)."""
    problems: List[str] = []
    if created:
        try:
            # A failed add may or may not have created it; delete only what exists.
            if read_trigger(call, SCRUB) is not None:
                call("delete_trigger", uuid=SCRUB)
                if read_trigger(call, SCRUB) is not None:
                    problems.append("the new slider item is still present")
        except Exception as error:
            problems.append("deleting the new slider item: %s" % error)
    for edit in reversed(edits):
        _restore(call, edit, problems)
    try:
        if not same(_strip(read_trigger(call, MEDIA)), _strip(plan["media_before"])):
            problems.append("Media differs from its backup outside the restored fields")
    except Exception as error:
        problems.append("final readback: %s" % error)
    return problems


def apply_plan(call: Call, plan: Dict[str, Any], *, backup: Callable[[Dict[str, Any]], Any],
               saved_check: Callable[[], List[str]],
               wait: Optional[Callable[[Callable[[], List[str]]], None]] = None) -> str:
    """Back up, write, verify and wait for the saved store; on any failure undo and raise InstallFailed."""
    before = plan["media_before"]
    if not same(read_trigger(call, MEDIA), before):
        raise Refusal("Media changed since it was inspected; no changes made.")
    path = backup({"media": before, "scrub": read_trigger(call, SCRUB), "edits": plan["edits"]})
    if not same(read_trigger(call, MEDIA), before):
        raise Refusal("Media changed during backup; no changes made. Backup: %s" % path)
    attempted: List[Edit] = []
    created = False
    try:
        # Each edit is recorded before its write: a call that errors may still have applied.
        config = [e for e in plan["edits"] if e["scope"] == "config" and e["uuid"] == PPAUSE]
        attempted += config
        _write(call, config, "new")
        if not plan["exists"]:
            attempted += [e for e in plan["edits"] if e["scope"] == "order"]
            created = True
            call("add_new_trigger", json=json.dumps(scrub_item(plan["page"], plan["order"])), parent_uuid=MEDIA)
        else:
            mine = [e for e in plan["edits"] if e["uuid"] == SCRUB]
            attempted += mine
            _write(call, mine, "new")
        # BTT may or may not renumber siblings on insert: converge on the planned map either way,
        # but only for orders this plan moves. Any other difference is someone else's edit.
        current = read_trigger(call, MEDIA)
        _check_media(current)
        moved = {e["uuid"]: e for e in plan["edits"] if e["scope"] == "order"}
        for child in current["BTTMenuItems"]:
            uid, planned = child["BTTUUID"], plan["final_orders"].get(child["BTTUUID"])
            if planned is None:
                raise RuntimeError("an unexpected item %s appeared in Media" % uid)
            if same(child.get("BTTOrder"), planned):
                continue
            if uid != SCRUB and not (uid in moved and same(child.get("BTTOrder"), moved[uid]["old"])):
                raise RuntimeError("the order of %s changed while installing" % uid)
            call("update_trigger", uuid=uid, json=json.dumps({"BTTOrder": planned}))
        verify(call, plan)
        (wait or wait_saved)(saved_check)
    except Exception as error:
        problems = rollback(call, plan, attempted, created)
        if problems:
            raise InstallFailed("%s\nROLLBACK INCOMPLETE: %s\nBackup: %s" % (error, "; ".join(problems), path),
                                True, path) from error
        raise InstallFailed("%s\nEvery changed field was restored. Backup: %s" % (error, path), False, path) from error
    return "Verified slider and PPause. Backup: %s" % path
