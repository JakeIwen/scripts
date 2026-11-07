# Lighting control

Framework-free light control for a BTT floating-menu Web View item (type 778).
The page is bundled by Python 3.9+ with no dependencies or external assets.

## Public interfaces

- Python: `build_page(lights: List[dict], cli_path: str) -> str` in `page.py`.
  `lights` contains the allowed entities and their labels/room labels. An initial
  state may be supplied, but opening always reads fresh state. `cli_path` is an
  absolute path to `macbook/scripts/van_lighting.py`.
- Browser: `LightControl.mount(rootElement, {lights, transport})` in `control.js`.
  Returns `{open(), refresh(options = {}), destroy()}`. `open()` resets selection
  to all available lights after a fresh read. `refresh({resetSelection: true})`
  has the same selection policy; plain `refresh()` preserves selection.
- Injected transport: `read()` and `write(entities, change)` return promises of
  `{ok: true, lights}` or `{ok: false, error}`. `LightControlState.Kind` in
  `state.js` owns the change vocabulary: `colour` with integer `hue`/`saturation`,
  `white` with integer `kelvin`, `brightness` with integer `brightness`, or
  `power` with boolean `on`. A write captures its target entities at release.
- BTT adapter: `LightingBridge.create(cliPath, runShellScript)` in `bridge.js`.
  It validates commands, quotes the CLI path and validates JSON stdout. The page
  does not call this bridge before the global `BTTInitialize()` callback.
  `BTTWindowWillBecomeVisible()` refreshes and resets selection on subsequent opens.

`state.js` owns selection, controls and the serialized queue. Colour and white
share one pending slot because they are mutually exclusive bulb modes; brightness
and power have independent slots. Pending changes replace only the same slot.
Failures are displayed, never retried. `control.js` owns DOM, picker events,
keyboard handling and swatches; `control.css` owns presentation. `preview.js`
and `preview_lights.json` are isolated browser fixtures, not live adapters.

## Preview and tests

From the repository root:

```
/usr/bin/python3 -B macbook/bettertouchtool/lighting_control/page.py --preview /absolute/path/preview.html
.local/test-venv/bin/python run_tests.py
```

Preview uses only an in-page fake with 450 ms latency; it never runs a shell or
contacts the dashboard. The controls fit a 300 × 430 Web View. Room chips scroll
when more lights are configured. Arrow keys adjust a focused colour/white picker;
left/right changes hue, up/down changes saturation, and Home/End selects range
endpoints. Keyboard writes happen on key release. Brightness uses a native slider.
No live BTT installation or light mutation is part of these tests.
