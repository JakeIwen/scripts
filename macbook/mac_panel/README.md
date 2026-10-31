# Mac Panel

Open <http://127.0.0.1:8769/> or run `macpanel` in Jacob's interactive shell.
The BTT menu-bar item and shortcut open the same page.

## Install and control

From `/Users/jacobr/dev/scripts`:

```sh
zsh macbook/mac_panel/install.zsh
```

The installer creates a standard-library-only Python environment at
`~/.local/share/mac-panel/venv` from `/opt/homebrew/bin/python3`, preserving a
broken previous environment beside it. Re-running installation safely regenerates
the LaunchAgent, including after an interpreter-path change. No sudo is used.

Use the following commands from any directory. `prepare` only generates and lints
configuration; it does not install a job. `status` is read-only and exits nonzero
unless installed configuration, enabled registration, launchd PID, HTTP health
and the private ownership record all agree.

```sh
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py prepare
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py install
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py status
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py start
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py stop
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py restart
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py verify
~/.local/share/mac-panel/venv/bin/python /Users/jacobr/dev/scripts/macbook/mac_panel/panel_service.py uninstall
```

`stop` disables and unloads the agent until `start`. `restart` and `verify` request
an authenticated graceful exit and prove KeepAlive starts a different PID and
instance. `uninstall` removes only registration; data and the environment remain.
Uninstall before moving the checkout. The agent starts at GUI login, not before
login at boot.

- Job: `gui/<uid>/com.jacobr.mac-panel`.
- Installed plist: `~/Library/LaunchAgents/com.jacobr.mac-panel.plist`.
- Prepared plist: `tmp/mac-panel/com.jacobr.mac-panel.plist` in the repository.
- Log: `tmp/mac-panel/panel.log` (private).
- Ownership record: `tmp/mac-panel/ownership.json` (mode 600; includes the instance
  token; do not paste it into reports). It is written only after a successful bind
  and removed by its owning instance on exit.

Python edits take effect only after a restart; the page's header says "Panel code
changed since it started" (from `/healthz` `source_changed`) and offers the same
confirmed restart as the services table. Static files are read per request.

## Sections and owners

| Section | Reads and commands |
| --- | --- |
| Deferred jobs | `/bin/zsh macbook/scripts/deferred.zsh list --json`; `cancel`, `retry`, `reschedule`, and `run-now` remain owned by that CLI. Run-now returns an asynchronous task receipt with start and finish times, shown in the job's row. Receipts are bounded and reset when the panel restarts; the job CLI retains durable job state. |
| Background services | `/bin/launchctl list` plus user `com.jacobr.*` plists; installed system daemons use `launchctl print system/<label>` and keep its stderr as the row's reason when it fails; "Could not find service" means not loaded. An unloaded job that `launchctl print-disabled` lists as disabled (what a local-service `stop` leaves) shows as grey "disabled" rather than a yellow warning. The inventory carries only each log's path, stream and modification time; `GET /api/services/logs/<label>` returns the bounded, redacted tails for one service when its disclosure is open. `/dev/null` streams are not listed. Confirmed user-agent restarts use `launchctl kickstart -k gui/<uid>/<label>`; system daemons are status-only. |
| User crontab | `/usr/bin/python3 -B macbook/scripts/crontab_sync.py status --json` reads the tracked and live crontabs. Per-job pause/resume and confirmed installation use the same CLI with the displayed revision; redirected log modification times are status hints, not proof that a job succeeded. The adapter derives each job's `program` (script name and arguments, without paths, redirections or interpreter flags) for the table. |
| llm-restore | `/usr/bin/python3 -I -B shared/sh/llm_restore.py --cron --json`; on/off adds `on` or `off` after `--cron`. This CLI owns its managed crontab block. |
| BTT guard | `/usr/bin/python3 -B macbook/bettertouchtool/btt.py guard`: `checkpoint --list`, `audit` (a fresh read-only audit on every section load), `service status`, `acceptance` (the scheduled monitor's pending and last automatic edit acceptance, shown as Auto-accept; drift pending acceptance shows yellow, not red), `inspect`. Explicit known-good acceptance uses `checkpoint --accept-current`, adding `--replace` only after a fresh active-checkpoint check. Repair is Terminal-only. |
| ntfy alerts | Reads only routing metadata from `~/.claude/settings.json` hooks and `~/.codex/config.toml` notify. Deferred and guard warnings use `macbook/scripts/ntfy_warning.py`; the destination value is never read or displayed. A confirmed test uses `guard notify-test --send`. |
| Settings | `shared/python/tool_settings.py` loads declarations from `shared/settings/` (plus `~/.config/jacobr/declarations/` for tools that live outside this repository) and revision-checked values from `~/.config/jacobr/`. The generated controls save through that module; reset sends declared defaults. A declaration's optional `label` is the form wording; the name stays the key. |
| Automations | `shared/python/tool_automation.py` loads declarations from `shared/automations/`. Each status command prints one validated `tool_automation.status_record`; declared actions run synchronously through the tool's fixed absolute argv and timeout, with confirmation when the declaration requires it. A declaration or status failure remains an error on that automation without taking down the section. |
| Amphetamine | `macbook/scripts/amphetamine_control.py status/configure` reads and updates Amphetamine's native automatic-lock settings. The checkbox and minutes field apply together. Enabling also allows the locked display to sleep and starts an indefinite keep-awake session. Disabling automatic lock preserves the current session. The battery cutoff is read-only. |

A new automation joins the panel by adding its declaration under
`shared/automations/` and a status command that prints a
`tool_automation.status_record`. The automation tool remains the owner of its
durable state and every mutation the declaration exposes.

### Amphetamine

Open `http://127.0.0.1:8769/#amphetamine`. Automatic lock operates only during
an active Amphetamine session. The panel reads native preferences on refresh,
so edits from Amphetamine's own preferences appear here too. Set the desired
minutes (1–240), check or uncheck automatic lock, and Apply. The displayed
session status distinguishes enabled locking from an active keep-awake session.

Applying restarts Amphetamine under a temporary `caffeinate -i` assertion,
which ends with the controller. It preserves an indefinite open-display
session's display and screen-saver choices. Timed, trigger-based, app/date-based
and closed-display sessions must be ended in Amphetamine first; the controller
refuses to replace them. The low-battery cutoff and lid behavior are unchanged.
Saved preferences are revision checked, backed up privately under
`~/.local/state/amphetamine-control/`, and the three edited keys plus the session
are restored on an apply failure. Native app control requires the Mac's login
session; an unavailable Apple Events connection is reported instead of claiming
the lock is active. This adds no service or background watcher.

macOS privacy permissions apply separately to this login agent. The controller
uses the panel's own Python interpreter so there is one Python identity to
authorize. If macOS denies access to Amphetamine's container, its Python process
needs approval to read app data (Full Disk Access is the manual settings route).
Apple Events also needs Automation permission to control Amphetamine. Granting
Full Disk Access covers other scripts using that interpreter; it is not limited
to this tab. These permissions require user action in System Settings and are
never granted, reset or bypassed by the controller. An unreadable preference file
is an error, never a report that automatic locking or the battery cutoff is off.

CLI paths above are relative to `/Users/jacobr/dev/scripts` for reference; the
server always invokes fixed absolute argv without a shell. Errors stay local to
their section. ntfy configuration remains owner-managed rather than panel-editable.

### Refresh behaviour

Deferred jobs and Background services poll every 15 seconds; every section has
its own Refresh button and "updated N s ago" line, and "Refresh all" refreshes
the rest. A poll (and "Refresh all") skips a section while it has unsaved edits,
an open editor, an armed confirmation or a focused field, and says so in that
line; nothing a reader typed is replaced by a render. Every 15 seconds the page
also reads `/healthz`: it reports an unreachable panel in the header, renews the
token and refreshes after a panel restart, and offers a restart when the Python
on disk is newer than the running process. Action results show in the section's
notice with a timestamp; successes clear after 20 seconds, errors stay.

Two-click buttons (restart, run now, cancel job, pause/resume/install crontab,
checkpoint, test alert, reset to defaults) arm on the first click and disarm on
Escape, blur or after 8 seconds. Reschedule opens an inline editor whose Apply
is enabled only once the time differs.

Each section is a tab in the sticky header. A tab shows its state dot (green
fine, yellow needs attention, red failing, grey idle, hollow unknown) and its
one-line summary as the hover tooltip; the arrow keys, Home and End move
between tabs. The URL hash names the open tab, so `#services` opens that tab
and in-page links such as crontab's llm-restore link switch tabs. Every section
loads at startup and the polled ones refresh whether or not their tab is open,
so the dots stay current; a section without auto-refresh reloads when its tab
opens more than 15 seconds after its last load. The open tab and the theme choice (system, light, dark)
are kept in the browser's `localStorage` only.

### Crontab actions

The Crontab section displays jobs from the whole tracked `macbook/crontab`,
including jobs paused with the exact `#off ` prefix. Pause and Resume submit the
revision that the page displayed, edit the tracked file atomically and install it
only when no live crontab lines would be lost; their confirmation names how many
repo-only lines that install also brings live. The llm-restore block has no row
actions because `llmrestore --cron off` and `llmrestore --cron on` own that switch;
its rows link to the llm-restore section. The codex-release-cleanup block remains
editable here.

**Install macbook/crontab** appears only while the live crontab differs from the
tracked file and is disabled, with the reason, while live-only lines make
installation unsafe; the page lists those lines and shows
`macbook/crontab has uncommitted changes` until another session commits the edit.
The CLI exits 0 on success, 2 for usage, 75 for a revision conflict or busy lock,
3 for a refused unsafe/managed/unknown/ambiguous operation, and 1 for another
error. The adapter maps both 75 and 3 to HTTP 409; invalid request bodies are HTTP
400 and unexpected tool failures are HTTP 502.

## Page

`static/` is served as ES modules with no build step; `server.py` serves
`index.html` at `/` and any `[a-z][a-z0-9_]*.mjs|.css` file that exists there.

- `app.mjs`: section registry (id, title, renderer, auto-refresh), tab and
  section construction, polling, open-tab and theme state, panel health.
- `panel_sections.mjs`: `refresh` (GET, keeps disclosure and focus state) and
  `action` (POST, notice, then refresh), plus the dirty check polls obey.
- `panel_api.mjs`: the only `fetch`; token header, JSON bodies, token renewal.
- `panel_dom.mjs`: elements, tables (each cell labelled for the narrow layout),
  armed buttons, notices, `time` nodes, key/value lists, focus keys.
- `relative_time.mjs`: pure time parsing and wording, tested under Node.
- `status_cell.mjs`: the `Level` vocabulary, status lines, tags and `worst`.
- `section_<name>.mjs`: one renderer per section returning `{node, summary, level}`.

`tests/test_static_page.py` parses every module with Node and checks that each
import names a served file.

## Security

The server binds only `127.0.0.1:8769`. Host headers must be exactly
`127.0.0.1:8769` or `localhost:8769`; every API request requires the per-instance
page token. Every POST additionally requires a matching Origin, JSON content type
and a body no larger than 64 KiB. `/healthz` is token-free but still Host-checked.
There are no CORS headers; a restrictive CSP disallows framing and inline scripts
and styles (the page uses module scripts, classes and CSS `attr()` only).

Child tools receive a minimal environment and timeouts. Logs and displayed
configuration are redacted, with all HTTP(S) URLs hidden because arbitrary ntfy
topics can be secrets. The panel never reads `pi/secrets/`, displays the Claude
settings `env` block, invokes BTT repair, or edits tool-owned files itself. Local
processes running as Jacob can access the page and token; this is a browser-origin
boundary, not isolation from other processes in the same account.

## Tests

The repository suite discovers tests in `macbook/mac_panel/tests/`:

```sh
.local/test-venv/bin/python run_tests.py
```

Its import registry supplies the flat panel modules and `shared/python`.
Tests use temporary configuration and fake tool outputs, never live launchd jobs,
crontabs, BTT state or Jacob's settings.
