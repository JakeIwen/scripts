"""Build the self-contained BTT track-scrub slider page; Python 3.9+, stdlib only."""
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
SCRIPTS = ("bridge.js", "controller.js", "view.js")
# The lifecycle hooks are BTT's documented Web View callbacks. Polling runs only while
# the bar is visible: every start has a matching stop on hide, close and visibilitychange.
BOOTSTRAP = """
(function () {
  const config = JSON.parse(document.getElementById('scrub-config').textContent);
  let controller = null;
  function newPageId() {
    const bytes = new Uint8Array(9);
    if (globalThis.crypto && crypto.getRandomValues) crypto.getRandomValues(bytes);
    else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    // base64url of random bytes matches the CLI's [A-Za-z0-9_-]{1,64} page ids.
    return 'p' + btoa(String.fromCharCode.apply(null, bytes)).replace(/\\+/g, '-').replace(/\\//g, '_').replace(/=+$/, '');
  }
  function build() {
    const transport = MediaScrubBridge.create(config.cli_path, options => runShellScript(options));
    const view = MediaScrubView.mount(document.getElementById('scrub-root'), {
      input: value => controller.dragMove(value), change: value => controller.release(value)});
    controller = MediaScrubController.create({transport, view, pageId: newPageId()});
  }
  const start = () => { if (controller) controller.start(); };
  const stop = () => { if (controller) controller.stop(); };
  globalThis.BTTInitialize = function () {
    if (!controller) build();
    if (!document.hidden) start();
  };
  globalThis.BTTWindowWillBecomeVisible = start;
  globalThis.BTTWillHideWindow = stop;
  globalThis.BTTWillCloseWindow = stop;
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
})();
"""


def _config(cli_path: str) -> str:
    if not isinstance(cli_path, str) or not cli_path.startswith("/") or any(
            ord(char) < 32 or ord(char) == 127 for char in cli_path):
        raise ValueError("Invalid media scrub CLI path")
    # JSON lives in a raw-text script element; closing tags must never escape it.
    return json.dumps({"cli_path": cli_path}, ensure_ascii=True).replace("</", "<\\/")


def build_page(cli_path: str) -> str:
    """One inline HTML document; its shell bridge stays idle until BTTInitialize."""
    config = _config(cli_path)
    css = (ASSETS / "control.css").read_text(encoding="utf-8")
    source = "\n".join("<script>\n" + (ASSETS / name).read_text(encoding="utf-8") + "\n</script>" for name in SCRIPTS)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Track scrub</title><style>" + css + "</style></head><body>"
            "<main id=\"scrub-root\"></main>"
            "<script id=\"scrub-config\" type=\"application/json\">" + config + "</script>"
            + source + "<script>" + BOOTSTRAP + "</script></body></html>\n")
