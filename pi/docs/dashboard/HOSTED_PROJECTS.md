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
key, `hosted_projects_schema`, is the integer schema version (currently 3).

```json
{
  "id": "pi:audiobooks.service",
  "services": [{"key": "pi:audiobooks.service", "live": true}],
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

- `id`: stable identifier. Entries a scan created use the key of the service
  that created them; manual entries use `custom-<uuid hex>`.
- `services`: the scanned services this entry stands for, each
  `{"key": "<host>:<unit>", "live": bool}`, sorted by key; empty for entries
  no scan has claimed. Mac keys are launchd labels (`mac:com.jacobr.fieldwork`),
  Pi keys are systemd unit names (`pi:audiobooks.service`), web keys are
  lionfeather-edge app names (`web:critter`). One app reached several ways
  (two units on one port, a Mac app and its public route, copies on the Mac
  and the Pi) is one entry with several services. A key belongs to at most
  one entry and is never removed from it, so keys, never ports, decide
  identity and a port change never resurrects a hidden entry.
- `name`: 1–80 printable characters. `description`: 0–200 characters.
- `host`: `mac`, `pi`, `web`, or `custom`. Display labels: This Mac, Vanpi,
  lionfeather.dev, Custom.
- `links`: one to four of `local` (127.0.0.1 on the Mac), `lan`
  (`vanpi.lan`), `ts` (the Pi's Tailscale IP), `web` (https). Same validation
  as before: http/https only, no credentials, never fetched by the backend.
- `status`: `new` (scanned, not yet reviewed), `listed`, or `hidden`. Hidden
  entries stay in the store so a rescan never re-proposes them.
- `source`: `scanned` or `manual` (form submissions and the one-time seed).
- `live`: at least one of the entry's services was in the last report from
  its host (always `true` for entries without services). `false` means stale;
  the sheet dims the row instead of deleting it.
- `first_seen`, `last_seen`: scan timestamps (UTC ISO 8601, `Z` suffix);
  `null` for entries no scan has claimed. A report that changes nothing
  writes nothing, so `last_seen` is the last scan that changed the entry (a
  new service, a link, or a service coming back), not the last scan that saw
  it, and the state file is not rewritten every 15 minutes. `updated_at`: last
  manual edit or creation.

### Migration

On first load with `hosted_projects_schema` absent or below 2 the store
upgrades in place and saves: legacy form entries (`host: "Custom"`, no
`status`) become `status: listed`, `source: manual`, `host: custom`,
no services, `live: true`; the former React catalog defaults are seeded as
`source: manual`, `status: listed` entries with no services (so the first
scan adopts them by port, see below) and deterministic ids
(`seed-<former catalog id>`). Seeding skips names already present
(case-insensitive).

Schema 3 (from 2) replaces each entry's `key` with `services`: a key becomes
one service carrying the entry's `live`, and `key: null` becomes an empty
list. Duplicate rows created under schema 2 are folded by the next scans
under rule 4 below, not by the migration, because only a scan knows a web
app's upstream port.

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

Web listeners may add `"service_port": 4317`, the explicit port in the edge
app's `service` URL. Edge apps keep the port they use on the Mac, which is
how a public route is tied to its Mac app.

`hosts` names every host the report fully covers; a host listed with no
listeners means everything scanned there is gone. Reconciliation runs under
the store lock:

1. **Group.** Listeners of one host that share an address (hostname and
   port of any link) are one app: the telemetry dashboard runs one systemd
   unit per bind address on port 8765. A group's primary listener is the one
   with the shortest key. Web groups are reconciled after Mac and Pi groups.
2. **Find the entry.** An entry holding any of the group's keys is the
   group's entry. If none does, the first match below, preferring entries
   Jacob has kept over unreviewed ones, among entries that hold no service of
   the group's host:
   1. an entry with a link at the same address as one of the group's links
      (this is how seeded and form entries are adopted, and how the Mac copy
      of Visual Guides joins the seeded row that already has its `local` link);
   2. for web groups, an entry with a `local` link on the group's
      `service_port` (Fieldwork, Electronics inventory);
   3. an entry holding a key on another host with the same name: the key
      after `<host>:`, without `com.jacobr.` or `.service`, so
      `mac:com.jacobr.visual-guides` and `pi:visual-guides.service` match.
3. **Create.** With no entry, create one with `status: new`, `source:
   scanned`, the primary listener's name and description, and `id` = its key.
   Creation never fails on a name collision: a name that matches an existing
   entry case-insensitively gets a host suffix (" (This Mac)", " (Vanpi)",
   " (lionfeather.dev)"), then " 2", " 3" and so on. The form and the update
   endpoint still reject collisions with HTTP 409.
4. **Fold.** An entry is foldable when a scan created it and Jacob has not
   kept it (`source: scanned`, status `new` or `hidden`). When the group's
   keys are spread over several entries, the first kept one is the target and
   every foldable one is folded into it. When all of them are foldable, a
   match from step 2 is the target if there is one. Folding moves the
   services onto the target, adds link kinds the target lacks, and deletes
   the folded entry. Two kept entries are never folded; each keeps its own
   keys.
5. **Claim.** Each listener's key is added to its entry if missing (an entry
   with no services takes the listener's host; otherwise the host stays),
   marked live, and the entry's links are refreshed. Name, description and
   status are never touched: addresses are facts, names and descriptions are
   Jacob's.

Refreshing links merges by kind: links whose kind the listener reports are
replaced when the address changed (an unchanged address keeps the stored URL,
path and label), and links of other kinds are kept.

Then every service whose host is in `hosts` and whose key was not reported
gets `live: false`, and an entry is live while any of its services is.
Entries of hosts not in `hosts` are untouched. The response is
`{"ok": true, "created": n, "adopted": n, "merged": n, "updated": n,
"stale": n}`: `adopted` counts keys added to an existing entry, `merged`
counts entries folded away, and `stale` counts only entries going from live
to stale. Report links may not repeat a kind. When `created > 0` the backend
sends one ntfy message through `VAN_DASHBOARD_NTFY_SEND` (default
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
  `service_port`: the explicit port of the app's `service` URL, omitted when
  the URL has none.

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
