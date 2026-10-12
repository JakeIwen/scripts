ObjC.import("Foundation");

function enabled(item) {
  return (
    item &&
    ["BTTEnabled", "BTTEnabled2"].every(
      (k) => item[k] === undefined || Number(item[k]) === 1,
    )
  );
}
function itemMatches(actual, wanted) {
  if (!enabled(actual) || Number(actual.BTTTriggerType) !== 773) return false;
  const actions = actual.BTTMenuItemActions || [],
    target = wanted.BTTMenuItemActions[0];
  return (
    actions.length === 1 &&
    enabled(actions[0]) &&
    Number(actions[0].BTTPredefinedActionType) === 206 &&
    actions[0].BTTShellTaskActionScript === target.BTTShellTaskActionScript &&
    actions[0].BTTShellTaskActionConfig === target.BTTShellTaskActionConfig
  );
}
function plan(parent, payload) {
  if (
    !enabled(parent) ||
    Number(parent.BTTTriggerType) !== 767 ||
    !Array.isArray(parent.BTTMenuItems)
  )
    throw Error(
      "Existing Performance Audio dropdown is missing or disabled; no changes made.",
    );
  const found = parent.BTTMenuItems.filter(
    (x) => x.BTTUUID === payload.item.BTTUUID,
  );
  if (found.length > 1)
    throw Error("Duplicate Custom conversion item; no changes made.");
  if (
    parent.BTTMenuItems.some(
      (x) =>
        x.BTTUUID !== payload.item.BTTUUID &&
        (x.BTTMenuConfig || {}).BTTMenuElementIdentifier ===
          "performance-custom",
    )
  )
    throw Error("Conflicting Custom conversion item; no changes made.");
  const previous = found[0],
    peer = parent.BTTMenuItems.find(
      (x) =>
        enabled(x) &&
        Number(x.BTTTriggerType) === 773 &&
        x.BTTUUID !== payload.item.BTTUUID,
    );
  if (!peer)
    throw Error(
      "No existing item available to match styling; no changes made.",
    );
  const desired = JSON.parse(JSON.stringify(payload.item)),
    style = peer.BTTMenuConfig || {};
  const font = String(style.BTTMenuAttributedText || "").match(/\\fs(\d+)/);
  if (!font)
    throw Error("Cannot determine current dropdown font; no changes made.");
  desired.BTTMenuConfig.BTTMenuAttributedText =
    desired.BTTMenuConfig.BTTMenuAttributedText.replace(
      /\\fs\d+/g,
      "\\fs" + font[1],
    );
  for (const key of [
    "BTTMenuItemMinWidth",
    "BTTMenuItemMaxWidth",
    "BTTMenuItemMinHeight",
    "BTTMenuItemMaxHeight",
  ])
    if (style[key] !== undefined) desired.BTTMenuConfig[key] = style[key];
  desired.BTTOrder = previous
    ? previous.BTTOrder
    : Math.max(-1, ...parent.BTTMenuItems.map((x) => Number(x.BTTOrder || 0))) +
      1;
  if (previous && !itemMatches(previous, desired))
    throw Error("Existing Custom conversion action differs; no changes made.");
  const layout = {};
  if (!previous && Number(parent.BTTMenuConfig.BTTMenuSizingBehavior) === 1)
    layout.BTTMenuFrameHeight =
      Number(parent.BTTMenuConfig.BTTMenuFrameHeight) +
      Number(style.BTTMenuItemMaxHeight) +
      Number(parent.BTTMenuConfig.BTTMenuVerticalSpacing || 0);
  if (Object.values(layout).some((x) => !Number.isFinite(x) || x <= 0))
    throw Error("Invalid existing menu size; no changes made.");
  return { desired, previous, layout };
}
function run(argv) {
  eval(
    ObjC.unwrap(
      $.NSString.stringWithContentsOfFileEncodingError(
        argv[1],
        $.NSUTF8StringEncoding,
        null,
      ),
    ),
  );
  const common = BTTCommon,
    payload = common.readJSON(argv[0]),
    btt = Application("/Applications/BetterTouchTool.app");
  function get(id) {
    const v = JSON.parse(btt.get_trigger(id)),
      list = Array.isArray(v) ? v : [v];
    const matches = list.filter((x) => x.BTTUUID === id);
    if (matches.length !== 1) throw Error("Cannot export " + id);
    return matches[0];
  }
  const parent = get(payload.parent),
    media = get(payload.media),
    change = plan(parent, payload);
  if (argv[3] !== "apply")
    return JSON.stringify({
      installed: !!change.previous,
      item: change.desired.BTTMenuName,
      layout: change.layout,
    });
  if (change.previous)
    return "Already configured: Tools > Performance Audio > Custom conversion…";
  const backup = common.backup(
    { parent, media },
    argv[2] + "/btt-custom-conversion-" + ObjC.unwrap($.NSUUID.UUID.UUIDString),
  );
  if (
    common.fingerprint(get(payload.parent)) !== common.fingerprint(parent) ||
    common.fingerprint(get(payload.media)) !== common.fingerprint(media)
  )
    throw Error("Menus changed during backup; no changes made.");
  btt.add_new_trigger(JSON.stringify(change.desired), {
    parent_uuid: payload.parent,
  });
  if (Object.keys(change.layout).length)
    btt.update_menu_item(payload.parent, {
      json: JSON.stringify(change.layout),
      persist: true,
    });
  const after = get(payload.parent),
    actual = after.BTTMenuItems.find(
      (x) => x.BTTUUID === change.desired.BTTUUID,
    );
  if (!itemMatches(actual, change.desired))
    throw Error("New item verification failed. Backup: " + backup);
  const clean = common.clone(after);
  clean.BTTMenuItems = clean.BTTMenuItems.filter(
    (x) => x.BTTUUID !== change.desired.BTTUUID,
  );
  for (const key of Object.keys(change.layout)) {
    if (clean.BTTMenuConfig[key] !== change.layout[key])
      throw Error("Menu size verification failed. Backup: " + backup);
    clean.BTTMenuConfig[key] = parent.BTTMenuConfig[key];
  }
  if (
    common.fingerprint(clean) !== common.fingerprint(parent) ||
    common.fingerprint(get(payload.media)) !== common.fingerprint(media)
  )
    throw Error("Unexpected change to existing menu. Backup: " + backup);
  return (
    "Verified: Tools > Performance Audio > Custom conversion… Backup: " + backup
  );
}
