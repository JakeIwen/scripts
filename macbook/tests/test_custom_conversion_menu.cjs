const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {createMediaToolsFixture, generateMenuDefinition, shellArguments} = require("./btt_test_support.cjs");
const folder = path.join(__dirname, "../bettertouchtool");
const mediaToolsFixture = createMediaToolsFixture();
const generated = generateMenuDefinition(mediaToolsFixture, "install_custom_conversion");
assert.deepEqual(shellArguments(generated.item.BTTMenuItemActions[0].BTTShellTaskActionScript), [
  "/usr/bin/python3",
  path.join(mediaToolsFixture.root, "build/Performance Audio.app/Contents/Resources/conversion_editor.py"),
  "--config", path.join(mediaToolsFixture.root, "scripts/performance_audio_presets.json"),
  "--finder-selection", "--background",
]);
const code = fs.readFileSync(path.join(folder, "install_custom_conversion.js"), "utf8");

function fixture(options = {}) {
  const peer = {
    BTTUUID: "peer",
    BTTTriggerType: 773,
    BTTOrder: 3,
    BTTMenuConfig: {
      BTTMenuAttributedText: "{\\rtf1\\fs28 Existing}",
      BTTMenuItemMaxHeight: 40,
      BTTMenuItemPaddingTop: 2,
      BTTMenuItemPaddingBottom: 2,
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
      BTTMenuFrameHeight: 44,
      BTTMenuVerticalSpacing: 4,
      BTTMenuAppearanceStyle: 7,
    },
  };
  const media = {
    BTTUUID: "media",
    BTTTriggerType: 767,
    BTTMenuItems: [{ BTTUUID: "unrelated" }],
  };
  const payload = JSON.parse(JSON.stringify(generated));
  payload.parent = "parent";
  payload.media = "media";
  payload.item.BTTUUID = "custom";
  payload.item.BTTMenuItemActions[0].BTTTriggerParentUUID = "custom";
  let writes = 0,
    backup = false,
    resizeFailures = options.resizeFailures || 0;
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
      if (resizeFailures-- > 0) throw Error("resize failed");
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
    media = JSON.stringify(f.media),
    appearance = f.parent.BTTMenuConfig.BTTMenuAppearanceStyle;
  assert.match(f.run(), /Verified/);
  assert.equal(f.writes(), 2);
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 92);
  assert.equal(JSON.stringify(f.peer), peer);
  assert.equal(JSON.stringify(f.media), media);
  assert.equal(f.parent.BTTMenuConfig.BTTMenuAppearanceStyle, appearance);
  assert.match(
    f.parent.BTTMenuItems[1].BTTMenuConfig.BTTMenuAttributedText,
    /\\fs28/,
  );
  assert.equal(f.parent.BTTMenuItems[1].BTTOrder, 4);
  assert.match(f.run(), /Already configured/);
  assert.equal(f.writes(), 2);
});
test("repairs a failed resize without duplicate item or repeated growth", () => {
  const f = fixture({ resizeFailures: 1 }),
    peer = JSON.stringify(f.peer),
    media = JSON.stringify(f.media),
    parentKeys = Object.keys(f.parent.BTTMenuConfig).sort();
  assert.throws(() => f.run(), /resize failed/);
  assert.equal(
    f.parent.BTTMenuItems.filter((x) => x.BTTUUID === "custom").length,
    1,
  );
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 44);
  assert.match(f.run(), /Verified/);
  assert.equal(
    f.parent.BTTMenuItems.filter((x) => x.BTTUUID === "custom").length,
    1,
  );
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 92);
  assert.equal(JSON.stringify(f.peer), peer);
  assert.equal(JSON.stringify(f.media), media);
  assert.deepEqual(Object.keys(f.parent.BTTMenuConfig).sort(), parentKeys);
  assert.match(f.run(), /Already configured/);
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 92);
  assert.equal(f.writes(), 3);
});
test("never shrinks an already sufficient fixed frame", () => {
  const f = fixture();
  f.parent.BTTMenuConfig.BTTMenuFrameHeight = 300;
  assert.match(f.run(), /Verified/);
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 300);
  assert.equal(f.writes(), 1);
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
  f.parent.BTTMenuConfig.BTTMenuFrameHeight = 300;
  f.run();
  assert.equal(f.parent.BTTMenuConfig.BTTMenuFrameHeight, 300);
  assert.equal(f.writes(), 1);
});
