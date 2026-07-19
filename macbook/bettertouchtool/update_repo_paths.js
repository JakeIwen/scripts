#!/usr/bin/osascript -l JavaScript

/*
 * Update enabled, non-Touch Bar BetterTouchTool triggers after this repository's
 * directory reorganization. The script is a dry run unless passed --apply.
 *
 * Touch Bar trigger IDs 629 and 642 are deliberately not queried. The queried
 * sets cover the path-bearing enabled configurations found during the 2026-07-18
 * audit: named triggers, other non-Touch Bar triggers, and Floating Menus.
 */

ObjC.import("Foundation");

const processArguments = $.NSProcessInfo.processInfo.arguments;
const args = [];
for (let index = 0; index < processArguments.count; index += 1) {
  args.push(ObjC.unwrap(processArguments.objectAtIndex(index)));
}
const applyChanges = args.includes("--apply");
const btt = Application("/Applications/BetterTouchTool.app");

const repoRoot = "/Users/jacobr/dev/scripts";
const replacements = [
  [`${repoRoot}/automation/display_settings.scpt`, `${repoRoot}/macbook/applescript/display_settings.scpt`],
  [`${repoRoot}/automation/toggle_sidecar.scpt`, `${repoRoot}/macbook/applescript/toggle_sidecar.scpt`],
  [`${repoRoot}/automation/sonosAudio.scpt`, `${repoRoot}/macbook/applescript/sonosAudio.scpt`],
  [`${repoRoot}/automation/wake_device.py`, `${repoRoot}/macbook/scripts/wake_device.py`],
  [`${repoRoot}/sh/`, `${repoRoot}/macbook/scripts/`],
  ["~/dev/scripts/sh/", "~/dev/scripts/macbook/scripts/"],
  [`${repoRoot}/sync_scripts.sh`, `${repoRoot}/pi/sync_scripts.sh`],
  [`${repoRoot}/python-automation`, `${repoRoot}/shared/python`],
  [`${repoRoot}/automation`, `${repoRoot}/shared/python`],
];

function rewriteString(value) {
  let rewritten = value;
  let replacementsMade = 0;
  for (const [oldValue, newValue] of replacements) {
    if (!rewritten.includes(oldValue)) continue;
    const pieces = rewritten.split(oldValue);
    replacementsMade += pieces.length - 1;
    rewritten = pieces.join(newValue);
  }
  return {value: rewritten, replacementsMade};
}

function rewriteValue(value) {
  if (typeof value === "string") return rewriteString(value);

  if (Array.isArray(value)) {
    let replacementsMade = 0;
    const rewritten = value.map((item) => {
      const result = rewriteValue(item);
      replacementsMade += result.replacementsMade;
      return result.value;
    });
    return {value: rewritten, replacementsMade};
  }

  if (value && typeof value === "object") {
    let replacementsMade = 0;
    const rewritten = {};
    for (const [key, item] of Object.entries(value)) {
      const result = rewriteValue(item);
      rewritten[key] = result.value;
      replacementsMade += result.replacementsMade;
    }
    return {value: rewritten, replacementsMade};
  }

  return {value, replacementsMade: 0};
}

const triggerQueries = [
  {label: "named trigger", parameters: {trigger_id: 643}},
  {label: "non-Touch Bar trigger", parameters: {trigger_id: 202}},
  {label: "Floating Menu", parameters: {trigger_id: 767}},
];

const seen = new Set();
let changedTriggers = 0;
let changedPaths = 0;

for (const query of triggerQueries) {
  const triggers = JSON.parse(btt.get_triggers(query.parameters));
  for (const trigger of triggers) {
    const uuid = trigger.BTTUUID || trigger.BTTTriggerUUID;
    if (!uuid || seen.has(uuid)) continue;
    seen.add(uuid);
    if (Number(trigger.BTTEnabled) === 0) continue;

    const result = rewriteValue(trigger);
    if (result.replacementsMade === 0) continue;

    const name = trigger.BTTTriggerName || trigger.BTTTriggerNameReadOnly || "unnamed";
    console.log(`${applyChanges ? "UPDATE" : "WOULD UPDATE"}: ${query.label} ${name} (${uuid}), ${result.replacementsMade} path(s)`);

    if (applyChanges) {
      btt.update_trigger(uuid, {json: JSON.stringify(result.value)});
    }
    changedTriggers += 1;
    changedPaths += result.replacementsMade;
  }
}

console.log(`${applyChanges ? "Updated" : "Would update"} ${changedTriggers} trigger(s), ${changedPaths} path occurrence(s).`);
if (!applyChanges) console.log("Dry run only. Pass --apply after reviewing this summary.");
