/* Compact slider row: elapsed, range, remaining, and one line for the title or a message.
   Everything shown is assigned through textContent; data never becomes markup. */
(function (root) {
  "use strict";
  function element(document, tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function mount(container, handlers) {
    const document = container.ownerDocument || root.document;
    const elapsed = element(document, "span", "time", "--:--");
    const remaining = element(document, "span", "time right", "--:--");
    const range = element(document, "input", "slider");
    range.type = "range";
    range.min = "0";
    range.step = "any";
    range.disabled = true;
    const line = element(document, "div", "line", "");
    const row = element(document, "div", "row");
    row.append(elapsed, range, remaining);
    const wrapper = element(document, "div", "scrub");
    wrapper.append(row, line);
    container.textContent = "";
    container.append(wrapper);
    // input fires while dragging, change on release; the controller keeps the thumb until then.
    range.addEventListener("input", () => handlers.input(Number(range.value)));
    range.addEventListener("change", () => handlers.change(Number(range.value)));
    function update(state) {
      range.disabled = state.disabled;
      range.max = String(state.max);
      if (Number(range.value) !== state.value) range.value = String(state.value);
      elapsed.textContent = state.elapsed;
      remaining.textContent = state.remaining;
      line.textContent = state.message || state.title || "";
      line.className = state.message ? "line message" : "line";
    }
    return {update};
  }
  root.MediaScrubView = Object.freeze({mount});
  if (typeof module !== "undefined") module.exports = root.MediaScrubView;
})(globalThis);
