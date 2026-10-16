# Hosted projects directory

[Pi documentation index](../../README.md) · [React dashboard](REACT_DASHBOARD.md)

The "Hosted projects" sheet in the van dashboard lists web apps running on
the Mac, the Pi, and lionfeather.dev. The Pi's dashboard state store is the
only source of truth. Each host scans itself on a schedule and reports its
listeners to the dashboard; new listeners appear in the sheet under "New"
until Jacob keeps, edits, or hides them. No LLM is involved anywhere.

## Store

Key `hosted_projects` in the dashboard `StateStore` JSON
(`~/.van_dashboard_state.json` on the Pi) holds a list of entries. A second
key, `hosted_projects_schema`, is the integer schema version (currently 2).

```json
{
  "id": "pi:audiobooks.service",
  "key": "pi:audiobooks.service",
  "name": "Audiobooks",
  "description": "Browse the audiobook library and play through Sonos.",
  "host": "pi",
  "links": [
    {"kind": "lan", "label": "LAN", "url": "http://vanpi.lan:8787/"},
    {"kind": "ts", "label": "Tailscale", "url": "http://100.82.91.76:8787/"}
  ],
  "status": "listed",
  "source": "scanned",
  "live": true,
  "first_seen": "2026-10-16T18:00:00Z",
  "last_seen": "2026-10-16T18:15:00Z",
  "updated_at": "2026-10-16T18:05:00Z"
}
```

- `id`: stable identifier. Scanned entries use their `key`; manual entries
  use `custom-<uuid hex>`.
- `key`: scanner identity, `<host>:<unit>`, or `null` for entries no scan has
  claimed. Mac keys are launchd labels (`mac:com.jacobr.fieldwork`), Pi keys
  are systemd unit names (`pi:audiobooks.service`), web keys are
  lionfeather-edge app names (`web:critter`). Keys, never ports, decide
  identity, so a port change never resurrects a hidden entry.
- `name`: 1–80 printable characters. `description`: 0–200 characters.
- `host`: `mac`, `pi`, `web`, or `custom`. Display labels: This Mac, Vanpi,
  lionfeather.dev, Custom.
- `links`: one to four of `local` (127.0.0.1 on the Mac), `lan`
  (`vanpi.lan`), `ts` (the Pi's Tailscale IP), `web` (https). Same validation
  as before: http/https only, no credentials, never fetched by the backend.
- `status`: `new` (scanned, not yet reviewed), `listed`, or `hidden`. Hidden
  entries stay in the store so a rescan never re-proposes them.
- `source`: `scanned` or `manual` (form submissions and the one-time seed).
- `live`: the last report from the entry's host included it. `false` means
  stale; the sheet dims the row instead of deleting it.
- `first_seen`, `last_seen`: scan timestamps (UTC ISO 8601, `Z` suffix);
  `null` for entries no scan has claimed. A report that changes nothing
  writes nothing, so `last_seen` is the last scan that changed the entry and
  the state file is not rewritten every 15 minutes. `updated_at`: last manual edit or
  creation.

### Migration

On first load with `hosted_projects_schema` absent or below 2 the store
upgrades in place and saves: legacy form entries (`host: "Custom"`, no
`status`) become `status: listed`, `source: manual`, `host: custom`,
`key: null`, `live: true`; the former React catalog defaults are seeded as
`source: manual`, `status: listed` entries with `key: null` (so the first
scan adopts them by port, see below) and deterministic ids
(`seed-<former catalog id>`). Seeding skips names already present
(case-insensitive).

## Scan reports

`POST /api/hosted-projects/scan` with a JSON body:

```json
{
  "hosts": ["mac", "web"],
  "scanned_at": "2026-10-16T18:15:00Z",
  "listeners": [
    {
      "key": "mac:com.jacobr.fieldwork",
      "host": "mac",
      "name": "Fieldwork",
      "description": "Job-search workspace.",
      "links": [{"kind": "local", "url": "http://127.0.0.1:4317/"}]
    }
  ]
}
```

`hosts` names every host the report fully covers; a host listed with no
listeners means everything scanned there is gone. Reconciliation, per
listener, under the store lock:

1. An entry with the same `key` exists: set `live: true`, `last_seen`, and
   refresh its links (addresses are facts, names and descriptions are
   Jacob's). Do not touch `name`, `description`, or `status`.
2. Otherwise an entry with `key: null` has a link whose hostname and port
   match one of the listener's links: adopt it (set `key`, set `host` to the
   listener's host, `source` stays, `live: true`, `first_seen`/`last_seen`),
   keep its name, description and status, refresh its links.
3. Otherwise create the entry with `status: new`, `source: scanned`, the
   listener's name and description, `id` = `key`. Creation never fails on a
   name collision: a name that matches an existing entry case-insensitively
   gets a host suffix (" (This Mac)", " (Vanpi)", " (lionfeather.dev)"),
   then " 2", " 3" and so on. The form and the update endpoint still reject
   collisions with HTTP 409.

Refreshing links merges by kind: links whose kind the listener reports are
replaced, links of other kinds are kept. An entry seeded with both a Mac
`local` link and Pi `lan`/`ts` links (Visual Guides) keeps its Mac link when
the Pi scan adopts it. The Mac listener for the same app then has no key-less
entry to adopt and appears as a separate new entry; hide it once.

Then every `scanned`-source entry whose host is in `hosts` and whose key was
not reported gets `live: false`. Entries of hosts not in `hosts` are
untouched. The response is `{"ok": true, "created": n, "adopted": n,
"updated": n, "stale": n}`; `stale` counts only live-to-stale transitions.
Report links may not repeat a kind. When `created > 0` the backend sends one ntfy
message through `VAN_DASHBOARD_NTFY_SEND` (default
`/home/pi/scripts/ntfy_send.sh`) titled "Van dashboard" with the new names,
in a background thread so the request never waits on it; a failed send is
logged and ignored.

The endpoint accepts at most 200 listeners, validates every field with the
store's rules, and rejects the whole report on any invalid field (HTTP 400)
so a partial report cannot mark good entries stale. The CSRF rule in
`http.py` applies unchanged: CLI clients send no Origin and need no header.

## Review endpoints

- `GET /api/hosted-projects` returns `{"ok": true, "projects": [...]}` with
  every entry, all statuses, sorted by name. `Cache-Control: no-store`.
- `POST /api/hosted-projects` (URL-encoded form, unchanged) adds a manual
  entry with `status: listed`.
- `POST /api/hosted-projects/<id>` with a JSON object containing any of
  `name`, `description`, `status` (`listed` or `hidden` only; nothing can be
  set back to `new`), and `links` (full replacement list) updates one entry
  and returns the full list. Unknown fields, unknown ids, and invalid values
  are HTTP 400 (404 for an unknown id). Browser callers send
  `X-Van-Dashboard: 1` with JSON as `postJson` already does.

## Scanners

`pi/apps/van_dashboard/hosted_project_scan.py` is a stdlib-only module
(Python 3.9+, no Flask import, importable without the rest of the package
initializing) with a CLI:

```
python3 -P -m pi.apps.van_dashboard.hosted_project_scan --host pi --post http://127.0.0.1:8788
python3 -P -m pi.apps.van_dashboard.hosted_project_scan --host mac --host web --post http://vanpi.lan:8788
python3 -P -m pi.apps.van_dashboard.hosted_project_scan --host mac --host web --print
```

`--print` writes the report JSON to stdout without posting. The exit code is
0 when the post succeeded or `--print` ran, 1 when a scan failed, 2 when the
dashboard refused or was unreachable. Each run logs one summary line.

- **pi**: running systemd services (`systemctl list-units --type=service
  --state=running`), each unit's `MainPID`, `Description`, and listening TCP
  sockets from `ss -ltnpH` (`sudo -n ss` first so other users' services such
  as Home Assistant resolve; fall back without sudo). A unit counts when it
  owns a socket bound to a non-loopback address on a port outside
  {22, 53, 139, 445, 1883, 5900}. Name: unit name without `.service`, title
  case, hyphens to spaces. Description: the unit `Description`, trimmed to
  200 characters. Links: `lan` `http://vanpi.lan:<port>/` and `ts`
  `http://<tailscale ip>:<port>/`, the IP from `tailscale ip -4` with
  `100.82.91.76` as the fallback. Multiple ports on one unit become one entry
  using the lowest port bound to a wildcard address, else the lowest port
  (Home Assistant also listens on 1400 and 40000 for integrations; its UI is
  the wildcard 8123). The dashboard's own 8788 is a normal entry.
- **mac**: `com.jacobr.*` plists under `/Library/LaunchDaemons` and
  `~/Library/LaunchAgents`; `launchctl print system/<label>` or
  `gui/<uid>/<label>` for the PID; `lsof -nP -iTCP -sTCP:LISTEN -a -p <pid>`
  for ports. Jobs without a listening port are skipped. Name: label without
  `com.jacobr.`, title case, hyphens to spaces. Description: the
  `description` field of `package.json` in the plist's `WorkingDirectory`
  when present, else empty. Links: `local` `http://127.0.0.1:<port>/`.
- **web**: apps in `/Users/jacobr/dev/lionfeather-edge/edge.json`
  (`--edge-config` overrides). Name: app name, title case. Description: the
  app's `note`, trimmed to 200. Links: `web` `https://<name>.lionfeather.dev/`.

Scanning is deterministic and idempotent: the same machine state produces
the same report, and reposting a report changes nothing.

## Scheduling

- Pi: a line in the `pi` user's crontab runs the pi scan every 15 minutes
  against `http://127.0.0.1:8788` with
  `PYTHONPATH=/home/pi/scripts/python-packages/current` and
  `PYTHONDONTWRITEBYTECODE=1` (release trees must stay byte-identical to
  their manifests), logging to `/home/pi/log/hosted_project_scan.log`.
- Mac: LaunchAgent `com.jacobr.hosted-project-scan`
  (`macbook/launchagents/`, installed by
  `macbook/scripts/install_hosted_project_scan.zsh`) runs the mac and web
  scans hourly and at load from the `/Users/jacobr/dev/scripts` checkout,
  posting to `http://vanpi.lan:8788` only, so nothing crosses the metered
  Tailscale uplink; off the van LAN the post fails quietly with exit 2.

## Sheet

`features/projects/` in the React frontend reads the store only; the static
catalog is gone. The sheet shows three groups: New (keep, edit, hide),
listed (edit, hide), and Hidden (collapsed, unhide). Rows carry the host
label, the description as the link title, and stale rows are dimmed with an
"offline" marker. Editing is inline: name and description, saved through the
update endpoint. The "New Hosted Project" form still adds manual entries.
