/* The only shell boundary. No display text becomes an argument. */
(function (root) {
  "use strict";
  const Kind = (typeof module !== "undefined" ? require("./state.js") : root.LightControlState).Kind;
  const ENTITY = /^light\.[a-z0-9_]+$/;
  // The dashboard's LightingController.VALID_STATES; "unknown" is its default for an unseen light.
  const STATES = ["on", "off", "unavailable", "unknown"];
  function integer(value, low, high, name) {
    if (!Number.isInteger(value) || value < low || value > high) throw new Error(`Invalid ${name}`);
    return String(value);
  }
  function quotePath(path) {
    if (typeof path !== "string" || !path.startsWith("/") || /[\x00-\x1f\x7f]/.test(path)) {
      throw new Error("Invalid lighting CLI path");
    }
    return "'" + path.replace(/'/g, "'\\''") + "'";
  }
  function buildCommand(cliPath, entities = null, change = null) {
    const prefix = `/usr/bin/python3 -B ${quotePath(cliPath)}`;
    if (entities === null && change === null) return `${prefix} lights`;
    if (!Array.isArray(entities) || !entities.length ||
        !entities.every(entity => typeof entity === "string" && ENTITY.test(entity))) {
      throw new Error("Invalid light selection");
    }
    if (!change || typeof change !== "object") throw new Error("Invalid lighting change");
    let args;
    switch (change.kind) {
      case Kind.colour:
        args = ["--hue", integer(change.hue, 0, 360, "hue"),
          "--saturation", integer(change.saturation, 0, 100, "saturation")];
        break;
      case Kind.white: args = ["--kelvin", integer(change.kelvin, 2000, 7000, "kelvin")]; break;
      case Kind.brightness: args = ["--brightness", integer(change.brightness, 1, 100, "brightness")]; break;
      case Kind.power:
        if (typeof change.on !== "boolean") throw new Error("Invalid power state");
        args = [change.on ? "--on" : "--off"];
        break;
      default: throw new Error("Invalid lighting change kind");
    }
    return [prefix, "set", ...new Set(entities), ...args].join(" ");
  }
  function parseOutput(output) {
    try {
      if (typeof output !== "string" || !output.trim()) throw new Error();
      const reply = JSON.parse(output);
      if (reply && reply.ok === false && typeof reply.error === "string" && reply.error.trim()) {
        return {ok: false, error: reply.error};
      }
      if (!reply || reply.ok !== true || !Array.isArray(reply.lights)) throw new Error();
      const seen = new Set();
      for (const light of reply.lights) {
        if (!light || typeof light.entity_id !== "string" || !ENTITY.test(light.entity_id) ||
            seen.has(light.entity_id) || typeof light.available !== "boolean" ||
            !STATES.includes(light.state)) throw new Error();
        seen.add(light.entity_id);
      }
      return {ok: true, lights: reply.lights};
    } catch (_) { return {ok: false, error: "Lighting command returned empty or invalid JSON"}; }
  }
  function create(cliPath, runShellScript) {
    quotePath(cliPath);
    async function execute(entities = null, change = null) {
      try {
        const script = buildCommand(cliPath, entities, change);
        const output = await runShellScript({script, launchPath: "/bin/zsh", parameters: "-c"});
        return parseOutput(output);
      } catch (failure) {
        return {ok: false, error: failure instanceof Error ? failure.message : String(failure)};
      }
    }
    return {read: () => execute(), write: (entities, change) => execute(entities, change)};
  }
  root.LightingBridge = Object.freeze({buildCommand, parseOutput, create});
  if (typeof module !== "undefined") module.exports = root.LightingBridge;
})(globalThis);
