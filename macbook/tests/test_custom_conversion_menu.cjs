const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync(
  require("node:path").join(
    __dirname,
    "../bettertouchtool/install_custom_conversion.js",
  ),
  "utf8",
);

function fixture(options = {}) {
  const peer = {
    BTTUUID: "peer",
    BTTTriggerType: 773,
    BTTOrder: 3,
    BTTMenuConfig: {
      BTTMenuAttributedText: "{\\rtf1\\fs28 Existing}",
      BTTMenuItemMaxHeight: 40,
    },
    BTTMenuItemActions: [
      { BTTUUID: "keep", BTTShellTaskActionScript: "untouched" },
    ],
  };
  const parent = {
    BTTUUID: "parent",
    BTTTriggerType: 767,
    BTTMenuItems: [peer],
    BTTMenuConfig: {
      BTTMenuSizingBehavior: 1,
      BTTMenuFrameHeight: 300,
      BTTMenuVerticalSpacing: 4,
    },
  };
  const media = {
    BTTUUID: "media",
    BTTTriggerType: 767,
    BTTMenuItems: [{ BTTUUID: "unrelated" }],
  };
  const payload = {
    parent: "parent",
    media: "media",
    item: {
      BTTUUID: "custom",
      BTTTriggerType: 773,
      BTTMenuName: "Custom conversion…",
      BTTMenuConfig: {
        BTTMenuElementIdentifier: "performance-custom",
        BTTMenuAttributedText: "{\\rtf1\\fs50 Custom conversion}",
      },
      BTTMenuItemActions: [
        {
          BTTPredefinedActionType: 206,
          BTTShellTaskActionScript: "editor --background",
          BTTShellTaskActionConfig: "shell",
        },
      ],
    },
  };
  let writes = 0,
    backup = false;
  const common = {
    readJSON: () => JSON.parse(JSON.stringify(payload)),
    clone: (x) => JSON.parse(JSON.stringify(x)),
    fingerprint: JSON.stringify,
    backup: () => {
      if (options.backupFails) throw Error("backup failed");
      backup = true;
      return "/backup";
    },
  };
  const btt = {
    get_trigger: (id) => JSON.stringify(id === "media" ? media : parent),
    add_new_trigger: (value, { parent_uuid }) => {
      assert.ok(backup);
      assert.equal(parent_uuid, "parent");
      writes++;
      parent.BTTMenuItems.push(JSON.parse(value));
    },
    update_menu_item: (id, { json, persist }) => {
      assert.ok(backup);
      assert.equal(persist, true);
      assert.equal(id, "parent");
      writes++;
      Object.assign(parent.BTTMenuConfig, JSON.parse(json));
    },
  };
  const $ = {
    NSString: {
      stringWithContentsOfFileEncodingError: () => "var BTTCommon=__common;",
    },
    NSUUID: { UUID: { UUIDString: "test" } },
  };
  const context = vm.createContext({
    $,
    ObjC: { import() {}, unwrap: (x) => x },
    Application: () => btt,
    __common: common,
  });
  vm.runInContext(code, context);
  return {
    run: (mode) =>
      context.run(["payload", "common", "/build", mode || "apply"]),
    parent,
    peer,
    media,
    writes: () => writes,
  };
}
test("adds one styled item, grows fixed menu and preserves prior actions", () => {
  const f = fixture(),
    peer = JSON.stringify(f.peer),
    media = JSON.stringify(f.media);
  assert.match(f.run(), /Verified/);
  assert.equal(f.writes(), 2);
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 344);
  assert.equal(JSON.stringify(f.peer), peer);
  assert.equal(JSON.stringify(f.media), media);
  assert.match(
    f.parent.BTTMenuItems[1].BTTMenuConfig.BTTMenuAttributedText,
    /\\fs28/,
  );
  assert.equal(f.parent.BTTMenuItems[1].BTTOrder, 4);
  assert.match(f.run(), /Already configured/);
  assert.equal(f.writes(), 2);
});
test("inspect and backup failure do not mutate", () => {
  const f = fixture();
  assert.equal(JSON.parse(f.run("inspect")).installed, false);
  assert.equal(f.writes(), 0);
  const blocked = fixture({ backupFails: true });
  assert.throws(() => blocked.run(), /backup failed/);
  assert.equal(blocked.writes(), 0);
});
test("refuses conflicting existing action", () => {
  const f = fixture();
  f.run();
  f.parent.BTTMenuItems[1].BTTMenuItemActions[0].BTTShellTaskActionScript =
    "foreign";
  assert.throws(() => f.run(), /differs/);
  assert.equal(f.writes(), 2);
});
test("content-sized menu remains content-sized", () => {
  const f = fixture();
  f.parent.BTTMenuConfig.BTTMenuSizingBehavior = 3;
  f.run();
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 300);
  assert.equal(f.writes(), 1);
});
