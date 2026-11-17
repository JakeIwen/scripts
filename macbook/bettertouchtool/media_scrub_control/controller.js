/* Polling, extrapolation and seek ordering; no DOM and no shell, so it runs under test.
   Seek ordering (R11): one seek CLI in flight plus the latest pending target. A CLI that
   reports ok:false clears the optimistic hold at once (no ack will follow); an ok:true CLI
   keeps it until the status shows a seek_ack for the same page and seq. */
(function (root) {
  "use strict";
  const POLL_MS = 500;
  const MESSAGE_MS = 3000;
  // Safety net only: the ack normally lands within a poll or two; a dead watcher must not
  // pin the thumb to a target forever.
  const ACK_TIMEOUT_MS = 10000;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  function formatClock(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600), minutes = Math.floor(total / 60) % 60, rest = total % 60;
    const two = number => String(number).padStart(2, "0");
    return hours ? `${hours}:${two(minutes)}:${two(rest)}` : `${minutes}:${two(rest)}`;
  }

  function create(options) {
    const {transport, view, pageId} = options;
    const timers = Object.assign({
      setTimer: (fn, ms) => setTimeout(fn, ms), clearTimer: id => clearTimeout(id),
      raf: fn => requestAnimationFrame(fn), caf: id => cancelAnimationFrame(id), now: () => Date.now(),
    }, options.timers);
    let active = false, polling = false, pollTimer = null, frame = null;
    let sample = null, sampleBase = 0;       // sampleBase: page clock when the sample's position was true
    let drag = null;                          // {value, media_id}
    let seq = 0, optimistic = null, inFlight = false, queued = null, message = null;

    const seekable = () => Boolean(sample && sample.seekable);
    function livePosition(now) {
      if (!sample) return 0;
      if (!sample.seekable || sample.state !== "PLAYING") return sample.position;
      const moved = sample.rate * (now - sampleBase) / 1000;
      return clamp(sample.position + moved, 0, sample.duration > 0 ? sample.duration : sample.position);
    }
    function setMessage(text) { message = {text, until: timers.now() + MESSAGE_MS}; }
    function clearOptimistic(text) {
      optimistic = null;
      if (text) setMessage(text);
    }
    function render() {
      const now = timers.now();
      if (optimistic && optimistic.confirmedAt !== null && now - optimistic.confirmedAt > ACK_TIMEOUT_MS) {
        clearOptimistic("Seek not confirmed");
      }
      if (message && now > message.until) message = null;
      const duration = sample ? sample.duration : 0;
      const value = drag ? drag.value : optimistic ? optimistic.position : livePosition(now);
      const shown = clamp(value, 0, duration > 0 ? duration : 0);
      view.update({
        disabled: !seekable(), max: duration, value: shown,
        elapsed: duration > 0 ? formatClock(shown) : "--:--",
        remaining: duration > 0 ? "-" + formatClock(duration - shown) : "--:--",
        title: sample ? sample.title : "Status unavailable",
        message: message ? message.text : "",
      });
    }
    function wantsFrames() {
      return active && sample !== null && sample.seekable && sample.state === "PLAYING";
    }
    function syncFrames() {
      if (!wantsFrames()) {
        if (frame !== null) { timers.caf(frame); frame = null; }
      } else if (frame === null) {
        frame = timers.raf(() => { frame = null; if (!active) return; render(); syncFrames(); });
      }
    }
    function accept(reply) {
      if (!reply || !reply.ok) { sample = null; return; }
      const ack = reply.seek_ack;
      if (optimistic && ack && ack.page === pageId && ack.seq === optimistic.seq) {
        clearOptimistic(ack.ok ? "" : ack.message || "Seek failed");
      } else if (optimistic && reply.media_id !== optimistic.media_id) {
        clearOptimistic("Track changed");
      }
      sample = reply;
      sampleBase = timers.now() - reply.age * 1000;
    }
    async function poll() {
      if (!active || polling) return;
      polling = true;
      let reply;
      try { reply = await transport.status(); } catch (_) { reply = {ok: false}; }
      polling = false;
      accept(reply);
      render();
      syncFrames();
      schedule();
    }
    function schedule() {
      if (active && pollTimer === null && !polling) {
        pollTimer = timers.setTimer(() => { pollTimer = null; poll(); }, POLL_MS);
      }
    }
    function start() {
      if (active) return;
      active = true;
      render();
      // A status call that outlived a stop() finishes into the new run and reschedules.
      if (!polling) poll();
    }
    function stop() {
      active = false;
      if (pollTimer !== null) { timers.clearTimer(pollTimer); pollTimer = null; }
      if (frame !== null) { timers.caf(frame); frame = null; }
    }
    async function send(request) {
      inFlight = true;
      let result;
      try { result = await transport.seek(request); } catch (_) { result = {ok: false, message: "Seek failed"}; }
      inFlight = false;
      if (optimistic && optimistic.seq === request.seq) {
        if (result && result.ok === true) optimistic.confirmedAt = timers.now();
        else clearOptimistic(result && result.message ? result.message : "Seek failed");
      }
      // An older request's outcome never touches newer optimism; the newer one goes next.
      if (queued) { const next = queued; queued = null; send(next); }
      render();
    }
    function dragMove(value) {
      if (!seekable() || !Number.isFinite(value)) return;
      if (!drag) drag = {media_id: sample.media_id, value};
      drag.value = clamp(value, 0, sample.duration);
      render();
    }
    function release(value) {
      const held = drag;
      drag = null;
      if (!seekable() || !Number.isFinite(value)) { render(); return; }
      if (held && held.media_id !== sample.media_id) {
        setMessage("Track changed");
        render();
        return;
      }
      const position = clamp(value, 0, sample.duration);
      const request = {page: pageId, seq: ++seq, position, source: sample.source, media_id: sample.media_id};
      optimistic = {seq: request.seq, position, media_id: sample.media_id, confirmedAt: null};
      message = null;
      if (inFlight) queued = request; else send(request);
      render();
    }
    return {start, stop, dragMove, release, render, isActive: () => active};
  }
  root.MediaScrubController = Object.freeze({create, formatClock, POLL_MS, ACK_TIMEOUT_MS});
  if (typeof module !== "undefined") module.exports = root.MediaScrubController;
})(globalThis);
