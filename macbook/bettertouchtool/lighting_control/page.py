"""Build the self-contained BTT light control; Python 3.9+, stdlib only."""
import argparse
import json
from pathlib import Path
import re
from typing import List


ASSETS = Path(__file__).resolve().parent
ENTITY = re.compile(r"light\.[a-z0-9_]+\Z")
SCRIPTS = ("vendor/iro-5.5.2.min.js", "state.js", "bridge.js", "control.js")
BOOTSTRAP = """
(function () {
  const config = JSON.parse(document.getElementById('lighting-config').textContent);
  let control = null;
  let ready = false;
  function mount(transport) {
    control = LightControl.mount(document.getElementById('lighting-root'), {lights: config.lights, transport});
  }
  globalThis.BTTInitialize = function () {
    if (!ready) {
      ready = true;
      mount(LightingBridge.create(config.cli_path, options => runShellScript(options)));
    }
    return control.open();
  };
  globalThis.BTTWindowWillBecomeVisible = function () {
    // Visibility can precede initialization; the first initialized read covers that opening.
    if (ready) return control.open();
  };
  PREVIEW_START
})();
"""


def _config(lights: List[dict], cli_path: str) -> str:
    if not isinstance(cli_path, str) or not cli_path.startswith("/") or any(
            ord(char) < 32 or ord(char) == 127 for char in cli_path):
        raise ValueError("Invalid lighting CLI path")
    seen = set()
    for light in lights:
        entity = light.get("entity_id")
        if not isinstance(entity, str) or not ENTITY.fullmatch(entity) or entity in seen:
            raise ValueError("Invalid or duplicate light entity")
        seen.add(entity)
    # JSON lives in a raw-text script element; closing tags must never escape it.
    return json.dumps({"lights": lights, "cli_path": cli_path}, ensure_ascii=True).replace("</", "<\\/")


def _document(lights: List[dict], cli_path: str, preview: bool = False) -> str:
    config = _config(lights, cli_path)
    css = (ASSETS / "control.css").read_text(encoding="utf-8")
    scripts = list(SCRIPTS) + (["preview.js"] if preview else [])
    source = "\n".join("<script>\n" + (ASSETS / name).read_text(encoding="utf-8") + "\n</script>" for name in scripts)
    startup = "mount(LightingPreview.create(config.lights)); ready = true; void control.open();" if preview else ""
    bootstrap = BOOTSTRAP.replace("PREVIEW_START", startup)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Lighting Colour</title><style>" + css + "</style></head><body>"
            "<main id=\"lighting-root\"><p role=\"status\">Waiting for BetterTouchTool…</p></main>"
            "<script id=\"lighting-config\" type=\"application/json\">" + config + "</script>"
            + source + "<script>" + bootstrap + "</script></body></html>\n")


def build_page(lights: List[dict], cli_path: str) -> str:
    """Return one inline HTML document; its shell bridge stays idle until BTTInitialize."""
    return _document(lights, cli_path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True, metavar="OUT.html",
                        help="write a browser preview with fixture lights and no shell calls")
    args = parser.parse_args(argv)
    fixtures = json.loads((ASSETS / "preview_lights.json").read_text(encoding="utf-8"))
    args.preview.write_text(_document(fixtures, "/preview/never-executed.py", preview=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
