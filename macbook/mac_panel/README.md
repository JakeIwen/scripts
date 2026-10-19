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

## Sections and owners

| Section | Reads and commands |
| --- | --- |
| Settings | `shared/python/tool_settings.py` loads declarations from `shared/settings/` and revision-checked values from `~/.config/jacobr/`. The generated controls save through that module; reset sends declared defaults. |
| Deferred jobs | `/bin/zsh macbook/scripts/deferred.zsh list --json`; `cancel`, `retry`, `reschedule`, and `run-now` remain owned by that CLI. Run-now returns an asynchronous task receipt. Receipts are bounded and reset when the panel restarts; the job CLI retains durable job state. |
| Background services | `/bin/launchctl list` plus user `com.jacobr.*` plists; installed system daemons use `launchctl print system/<label>`. Log tails are bounded and redacted. Confirmed user-agent restarts use `launchctl kickstart -k gui/<uid>/<label>`; system daemons are status-only. |
| User crontab | `/usr/bin/crontab -l`, compared with `macbook/crontab`. Shows schedules, managed ownership and redirected log modification times, not proof that a job succeeded. |
| llm-restore | `/usr/bin/python3 -I -B shared/sh/llm_restore.py --cron --json`; on/off adds `on` or `off` after `--cron`. This CLI owns its managed crontab block. |
| BTT guard | `/usr/bin/python3 -B macbook/bettertouchtool/btt.py guard`: `checkpoint --list`, `last-audit`, `service status`, `inspect`, `audit`. Explicit known-good acceptance uses `checkpoint --accept-current`, adding `--replace` only after a fresh active-checkpoint check. Repair is Terminal-only. |
| ntfy alerts | Reads only routing metadata from `~/.claude/settings.json` hooks and `~/.codex/config.toml` notify. Deferred and guard warnings use `macbook/scripts/ntfy_warning.py`; the destination value is never read or displayed. A confirmed test uses `guard notify-test --send`. |

CLI paths above are relative to `/Users/jacobr/dev/scripts` for reference; the
server always invokes fixed absolute argv without a shell. Services and deferred
jobs refresh about every 15 seconds. Other sections refresh on demand and after
actions. Errors stay local to their section. Crontab editing and ntfy configuration
editing are not built yet; those remain owner-managed files.

## Security

The server binds only `127.0.0.1:8769`. Host headers must be exactly
`127.0.0.1:8769` or `localhost:8769`; every API request requires the per-instance
page token. Every POST additionally requires a matching Origin, JSON content type
and a body no larger than 64 KiB. `/healthz` is token-free but still Host-checked.
There are no CORS headers; a restrictive CSP disallows framing and inline scripts.

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
