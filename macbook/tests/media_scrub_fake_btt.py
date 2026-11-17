"""In-memory BTT socket holding a Media tree, for the media-scrub installer tests.

Models the calls the installer makes (get_trigger, update_menu_item, update_trigger,
add_new_trigger with parent_uuid, delete_trigger). `renumber` switches between the two
insert behaviours the installer must converge on: BTT shifting later siblings itself,
or leaving every order as written.
"""
import copy
import json

from macbook.bettertouchtool.btt_common import MEDIA
from macbook.bettertouchtool.media_scrub_install import PPAUSE

OLD_SCRIPT = "// ssh pi@vanpi.lan play_status"


def media_fixture():
    def button(uid, order, name):
        return {"BTTUUID": uid, "BTTTriggerType": 773, "BTTOrder": order, "BTTMenuName": name,
                "BTTTriggerParentUUID": MEDIA, "BTTMenuConfig": {"BTTMenuElementIdentifier": name}}
    ppause = button(PPAUSE, 2, "PPause")
    ppause["BTTMenuConfig"].update({
        "BTTMenuItemScriptActive": 1, "BTTMenuScriptUpdateInterval": 2,
        "BTTMenuScriptSettings": {"BTTScriptType": 3, "BTTAppleScriptString": OLD_SCRIPT,
                                  "BTTScriptFunctionToCall": "itemScript", "BTTScriptLocation": 0}})
    ppause["BTTMenuItemActions"] = [{"BTTUUID": "play-action", "BTTNamedTriggerToTrigger": "PlayPause"}]
    return {"BTTUUID": MEDIA, "BTTTriggerType": 767, "BTTLastUpdatedAt": 1.0,
            "BTTMenuConfig": {"BTTMenuElementIdentifier": "Media", "BTTMenuVerticalSpacing": 2.5},
            "BTTMenuItems": [button("prev", 0, "Prev"), button("rew", 1, "Rew"), ppause,
                             button("fwd", 3, "Fwd"), button("row", 4, "Row"), button("tools", 5, "Tools")]}


class FakeMediaBTT:
    def __init__(self, renumber=False, honor_order=True, media=None):
        self.media = media if media is not None else media_fixture()
        self.renumber, self.honor_order = renumber, honor_order
        self.log, self.before = [], None  # before(command, parameters) may raise or edit state

    def find(self, uid):
        stack = [self.media]
        while stack:
            node = stack.pop()
            if node["BTTUUID"] == uid:
                return node
            stack.extend(node.get("BTTMenuItems", []))
        return None

    def parent_of(self, uid):
        for node in [self.media] + self.media["BTTMenuItems"]:
            if any(child["BTTUUID"] == uid for child in node.get("BTTMenuItems", [])):
                return node
        return None

    def __call__(self, command, **parameters):
        self.log.append((command, parameters))
        if self.before:
            self.before(command, parameters)
        if command == "get_trigger":
            node = self.find(parameters["uuid"])
            return json.dumps([copy.deepcopy(node)] if node else {})
        if command == "update_menu_item":
            assert parameters["persist"] == "true"
            self.find(parameters["uuid"])["BTTMenuConfig"].update(json.loads(parameters["json"]))
        elif command == "update_trigger":
            node, patch = self.find(parameters["uuid"]), json.loads(parameters["json"])
            node.update(patch)
            # Like BTT: an omitted enabled flag becomes 1, and an export omits a flag that is 1.
            for key in ("BTTEnabled", "BTTEnabled2"):
                if key not in patch:
                    node[key] = 1
                if node[key] == 1:
                    del node[key]
        elif command == "add_new_trigger":
            item = json.loads(parameters["json"])
            parent = self.find(parameters["parent_uuid"])
            siblings = parent["BTTMenuItems"]
            order = item["BTTOrder"] if self.honor_order else 1 + max(s["BTTOrder"] for s in siblings)
            if self.renumber:
                for sibling in siblings:
                    if sibling["BTTOrder"] >= order:
                        sibling["BTTOrder"] += 1
            item["BTTOrder"] = order
            item["BTTTriggerName"] = "Webview Menu Item: " + item["BTTMenuName"]
            siblings.append(item)
        elif command == "delete_trigger":
            parent = self.parent_of(parameters["uuid"])
            gone = self.find(parameters["uuid"])
            parent["BTTMenuItems"].remove(gone)
            if self.renumber:
                for sibling in parent["BTTMenuItems"]:
                    if sibling["BTTOrder"] > gone["BTTOrder"]:
                        sibling["BTTOrder"] -= 1
        return ""

    def orders(self):
        return {child["BTTUUID"]: child["BTTOrder"] for child in self.media["BTTMenuItems"]}

    def commands(self, *names):
        return [entry for entry in self.log if entry[0] in names]
