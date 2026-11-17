/* The only shell boundary. The seek request travels as one base64 token of
   JSON.stringify(request), so no page-supplied character reaches the shell. */
(function (root) {
  "use strict";
  const PAGE = /^[A-Za-z0-9_-]{1,64}$/;
  const SOURCES = ["vantv", "vlc"];

  function quotePath(path) {
    if (typeof path !== "string" || !path.startsWith("/") || /[\x00-\x1f\x7f]/.test(path)) {
      throw new Error("Invalid media scrub CLI path");
    }
    return "'" + path.replace(/'/g, "'\\''") + "'";
  }
  function validRequest(request) {
    return Boolean(request) && typeof request.page === "string" && PAGE.test(request.page) &&
      Number.isInteger(request.seq) && request.seq >= 0 &&
      Number.isFinite(request.position) && request.position >= 0 &&
      SOURCES.includes(request.source) &&
      typeof request.media_id === "string" && request.media_id.length > 0 && request.media_id.length <= 512;
  }
  function base64Utf8(text) {
    // btoa takes Latin-1 only; titles and ids may be any Unicode.
    let binary = "";
    for (const byte of new TextEncoder().encode(text)) binary += String.fromCharCode(byte);
    return btoa(binary);
  }
  const prefix = cliPath => `/usr/bin/python3 -B ${quotePath(cliPath)}`;
  function statusCommand(cliPath) {
    return `${prefix(cliPath)} status`;
  }
  function seekCommand(cliPath, request) {
    if (!validRequest(request)) throw new Error("Invalid seek request");
    const body = {page: request.page, seq: request.seq, position: request.position,
      source: request.source, media_id: request.media_id};
    // Base64 holds no quote, so single quotes keep '=' padding literal for zsh.
    return `${prefix(cliPath)} seek --b64 '${base64Utf8(JSON.stringify(body))}'`;
  }
  const finite = (value, fallback = 0) => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
  function lastJsonLine(output) {
    if (typeof output !== "string") throw new Error("no output");
    const lines = output.split("\n").map(line => line.trim()).filter(Boolean);
    if (!lines.length) throw new Error("empty output");
    return JSON.parse(lines[lines.length - 1]);
  }
  function parseAck(ack) {
    if (ack && typeof ack.page === "string" && Number.isInteger(ack.seq) && typeof ack.ok === "boolean") {
      return {page: ack.page, seq: ack.seq, ok: ack.ok, message: typeof ack.message === "string" ? ack.message : ""};
    }
    return null;
  }
  function parseStatus(output) {
    try {
      const reply = lastJsonLine(output);
      if (!reply || typeof reply !== "object" || typeof reply.ok !== "boolean") throw new Error("shape");
      if (!reply.ok) return {ok: false, error: typeof reply.error === "string" ? reply.error : "Status unavailable"};
      const rate = finite(reply.rate, 1);
      return {ok: true, source: typeof reply.source === "string" ? reply.source : "none",
        state: typeof reply.state === "string" ? reply.state : "",
        title: typeof reply.title === "string" ? reply.title : "",
        position: Math.max(0, finite(reply.position)), duration: Math.max(0, finite(reply.duration)),
        rate: rate > 0 ? rate : 1, media_id: typeof reply.media_id === "string" ? reply.media_id : null,
        seekable: reply.seekable === true, age: Math.max(0, finite(reply.age)),
        seek_ack: parseAck(reply.seek_ack)};
    } catch (_) { return {ok: false, error: "Status unavailable"}; }
  }
  // The CLI prints {"ok","superseded","message"} and exits 1 when it refuses; only ok === true counts.
  function parseSeek(output) {
    try {
      const reply = lastJsonLine(output);
      if (!reply || typeof reply !== "object") throw new Error("shape");
      return {ok: reply.ok === true, message: typeof reply.message === "string" ? reply.message : ""};
    } catch (_) { return {ok: false, message: "Seek failed"}; }
  }
  function create(cliPath, runShellScript) {
    quotePath(cliPath);
    const run = script => runShellScript({script, launchPath: "/bin/zsh", parameters: "-c"});
    return {
      async status() {
        try { return parseStatus(await run(statusCommand(cliPath))); }
        catch (_) { return {ok: false, error: "Status unavailable"}; }
      },
      async seek(request) {
        try { return parseSeek(await run(seekCommand(cliPath, request))); }
        catch (failure) { return {ok: false, message: "Seek failed"}; }
      },
    };
  }
  root.MediaScrubBridge = Object.freeze({statusCommand, seekCommand, parseStatus, parseSeek, create});
  if (typeof module !== "undefined") module.exports = root.MediaScrubBridge;
})(globalThis);
