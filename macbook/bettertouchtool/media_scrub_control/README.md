# Media scrub control

Framework-free track-scrub slider for the Media bar's Web View item (type 778, about
220 x 40 px, light-mode blurred bar). Python 3.9+ builds one inline document; no
dependencies or external assets. It shows elapsed time, a range input and remaining
time for media playing on the Pi, and a second line with the title or a short message.

## Public interfaces

- Python: `build_page(cli_path: str) -> str` in `page.py`. `cli_path` is the absolute path
  of `macbook/scripts/media_scrub.py`; `install_media_scrub.py` supplies its own checkout's.
- `bridge.js` (`MediaScrubBridge`): the only shell boundary. `statusCommand`/`seekCommand`
  build `/usr/bin/python3 -B '<path>' status` and `... seek --b64 '<token>'`, where the token
  is the base64 of `JSON.stringify({page, seq, position, source, media_id})`, so no page text
  reaches the shell. Requests are validated first. `create(cliPath, runShellScript)` runs
  them through `runShellScript({script, launchPath: "/bin/zsh", parameters: "-c"})` and
  validates the JSON each prints; a CLI that does not print `ok: true` counts as failed.
- `controller.js` (`MediaScrubController.create({transport, view, pageId, timers})`): no DOM
  and no shell. `start()`/`stop()` are idempotent. Polling: one `status` every 500 ms,
  scheduled only after the previous reply, so calls never overlap. While PLAYING and seekable a
  `requestAnimationFrame` loop extrapolates position by `rate`, counted from the sample's
  `age`, clamped to the duration; it stops when paused, not seekable or stopped.
- `view.js` (`MediaScrubView.mount`): the DOM. Everything shown goes through `textContent`.

## Seek ordering

Dragging only moves the thumb. Release sends one seek: a random page id and a `seq`
counter identify it, at most one seek CLI runs, and a newer release replaces the pending
target. The thumb holds the target until the status shows a `seek_ack` for this page and
the current `seq`; older acks and results never touch newer optimism. A seek CLI that
reports `ok: false` (superseded, refused, stale, a failed or garbled run) clears the hold
immediately, since no ack will follow; so does an ack with `ok: false` or a changed
`media_id`. A hold that is never confirmed ends after 10 s. Releasing after the track
changed mid-drag sends nothing.

## Lifecycle

`BTTInitialize` builds the page and starts only if the document is visible;
`BTTWindowWillBecomeVisible` and a visible `visibilitychange` start it; `BTTWillHideWindow`,
`BTTWillCloseWindow` and a hidden `visibilitychange` stop the poll timer and the frame loop.
The installer sets `BTTMenuItemKeepActiveInBackground` to 0 so BTT does not keep the page
running while the bar is closed.

## Tests

`macbook/tests/test_media_scrub_control.cjs` (controller, bridge, view on fake time),
`test_media_scrub_page.py` with `media_scrub_page_harness.cjs` (built page and its lifecycle
hooks in a stub DOM). Run from the repository root:

```
.local/test-venv/bin/python run_tests.py --path macbook/tests
```
