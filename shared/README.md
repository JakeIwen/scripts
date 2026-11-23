# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH`. The Pi's broad sync copies `sh/` executables flat into
`/home/pi/scripts/`. Pi package releases preserve its package path.
Pi shell utilities and the packaged video service include the current release's
`shared/python` for standalone `sonos_tasks` imports; changes to that helper are
part of the video's restart digest. `sns.sh` preserves inherited paths for the
frozen flat video's saved-unit rollback. No current deployer writes the retired
`/home/pi/scripts/python-automation/` tree. See
[Pi deployment and rollback](../pi/docs/deployment.md).

Compute-specific cross-host modules live in the standalone sibling checkout at
`/Users/jacobr/dev/van-compute/`. They are deployed atomically by that checkout's
compute installer to `/home/pi/van_compute/`; the general Pi sync invokes the
coupled installer after package deployment and does not copy compute files through
its generic staging paths.

## Dependency install guard

`sh/dep_install_guard.py` checks explicit public package operands in Bash tool
calls: npm, pnpm, yarn and bun installs and package runners; pip, pipx, uv and
Poetry additions. No packages are installed by the hook. Unknown names stop at
one authoritative registry probe: they are never sent to downloads or malware
services. Private or uncertain source configuration and offline installs skip
public checks, including cached facts. An unversioned `npx`/`npm exec` command
that selects a project or global executable is not an install. Existing local
files selected by `pipx run` are not registry requests either.

| Evidence | Action |
|---|---|
| Public registry 404 | Block; override only for a private package known to exist |
| OSV malware advisory for the entire package or pinned version | Block, no inline override |
| Version-specific malware advisory, no exact pin | Block; pin an exact version outside every affected set |
| Exact pin outside the reported versions | Warn; if the version check was incomplete, say so |
| First release under 90 days old and fewer than 1,000 measured weekly downloads | Block unless the operator explicitly confirms the new, little-used package |
| First release under 14 days old and no download statistics | Same override; 14–89 days with no statistics only warns |

When a deny permits it, put `DEP_GUARD_ALLOW=<canonical-name>` (or comma-separated
names) on that install command. Inline text cannot disable the hook or override
malware. An operator can run a deliberate exception themselves, or disable the
hook via `DEP_INSTALL_GUARD=off` in its *own* environment.

The PyPI cutoffs are provisional. Low use alone does not block. Registries can
be wrong or unavailable, and age and popularity do not establish trust. At
50,000+ weekly downloads, the hook does not fetch release metadata: a young,
popular package will not get an age warning. Otherwise, it warns about new
packages and npm packages with at most two versions and just one maintainer.
A failed check adds context without erasing an independently established deny.

**Not checked:** bare installs (including `npm ci`, `uv sync` and
`poetry install`), requirements/constraint files, manifest edits followed by
bare installs, package names inside scripts, remote commands over SSH, and
non-registry specs. In a compound shell command, an unrecognized or potentially
mutating predecessor (including another install or a write to a file) makes later
installs unchecked. Redirects to `/dev/null`, `/dev/stdout`, `/dev/stderr` or an
open descriptor do not write a named file. Before its first command operand,
`npx` accepts only `-y`, `--yes`, `-p NAME`, `--package NAME` or `--package=NAME`
for checking; other switches make its operand boundary uncertain.
Run an install in a separate tool call to check it against the resulting filesystem.
Check skipped names yourself against the project's own documentation. This is a
best-effort tool-call check, not a package-manager or shell security boundary.

The cache lives in `dep-install-guard/lookups.json` under an absolute
`XDG_CACHE_HOME`, otherwise under `~/.cache`; it is capped at 1 MiB and 2,000
entries. Per call: 20 distinct names, 40 requests, 8 MiB, six concurrent requests
and a five-second deadline for the entire evaluation, including filesystem
reads. Confirmed public names proceed independently of stalled registries;
unexpired, confirmed malware evidence still applies if existence cannot be
refreshed. Successful refreshes apply to every invocation of the same name;
a failed refresh leaves an unmarked invocation's valid cache evidence intact.
The override itself applies only to its own invocation. The hook must run with
its lexical reader and all `dep_guard_*.py` siblings beside it. On an isolated
check only, `DEP_INSTALL_GUARD_TRACE` in the hook environment can point to a
private temporary trace of destination host, operation class and byte count.
Remove the trace afterwards. For a repo-level release-age gate, consider pnpm
`minimumReleaseAge` or npm's `--before <date>`.

## Session search

`claudesearch`, `codexsearch`, and `llmsearch` search newest sessions first.
Results are grouped by default: `claudesearch`/`codexsearch` show one result per
session and `llmsearch` combines sessions by directory and tool, each with its
match count and latest hit. `-e` / `--single` lists every match instead (each
session's newest excerpt first; `llmsearch` interleaves both tools by session
activity); a later `-g` / `--group` undoes it. Indexed results retain the
index's newest-match ordering.

During a grouped grep scan, terminal listings update as sessions are
found and counts accumulate. The final listing is complete; redirected or piped
output contains only that final listing, without terminal control sequences.
Result headers are soft orange (256-colour 215) on terminals. Set `NO_COLOR=1`
to disable colour; pipes, files and JSON records are always plain.

`-t` / `--title` (or `-n` / `--name`) searches only saved session titles for a
literal, case-insensitive term, for example `llmsearch -n "backup"`. It returns
one hit per session and is grouped like other searches. Title searches read the session stores directly
without using the transcript index; untitled sessions do not match.

`-s` / `--session` looks sessions up by id, the whole id or its start (for
example `llmsearch -s a6ac05ee`), also straight from the session stores.

`-f` / `--forks` shows each matching session's fork family instead of its
matches: a tree from the oldest ancestor down through every fork and branch,
with the matching sessions marked, or `no forks or branches`. It combines with
any search, for example `llmsearch -n 'substring matching' -f`. Codex subagent
threads are forks too, but they are left out unless they match.

Ctrl+C stops any search with a one-line `aborted` message and exit status 130.

`-N` (for example `-2`) shows N lines before and after each match within its
message. `-a N` / `-aN` and `-b N` / `-bN` set after/before lines separately;
later flags override each side. Counts are non-negative integers. Overlapping
or adjacent windows merge; `--` separates gaps. Long lines are cropped to about
300 characters around the match. Without context flags, excerpts are unchanged.
Context flags do not affect title searches; flags after `--` are search text.
An older index that rejects context flags falls back to grep with a notice.

The scripts require Bash, grep, awk, jq (index mode), and Python 3.9 or newer.
Keep all nine `sh/*search*` files together when deploying them to
`/home/pi/scripts/`, including `session_search_extract.py` and
`session_search_excerpt.awk`. Grep mode parses each selected transcript once in
one Python process; awk renders a whole file's excerpts in one batch, retaining
each provider's original locale and UTF-8 window behavior.

## Sonos CLI (`sns`)

The Mac alias and BTT call `macbook/scripts/sns.sh`. It runs the existing
`~/py3env/bin/python` directly, not PATH's Python and not a Pi-failure fallback.
`SNS_PYTHON` can explicitly select another SoCo environment. The Pi wrapper
uses its `python3` and the package release's shared CLI. Neither wrapper builds
Python source from arguments, retries failed operations, or changes ALSA volume.

- Use `sns pink_noise` or `sns brown_noise`; task names require underscores.
- `sns --help` lists commands without importing SoCo.
- `sns --check pink_noise` validates the command without discovering speakers.
- `sns --doctor` shows the actual interpreter, SoCo version and module path.
- `sns --devices` discovers speakers and prints their identities; read only.
- `sns --pi <command>` explicitly uses SSH's `vanpi` auto-route. Arguments are
  quoted for the remote shell; the Mac's interactive `rpi` alias is not used.
- `sns --local <command>` explicitly selects the Mac (the default).

The existing Mac venv is isolated (`include-system-site-packages = false`) and
was verified with Python 3.13.2 and SoCo 0.30.10; Pi uses Python 3.11.2 and SoCo
0.30.4. Shared dispatch/tasks support Python 3.9+. To provision a missing Mac
venv, use `python3 -m venv ~/py3env`, then
`~/py3env/bin/python -m pip install 'soco==0.30.10'`.
Check dependencies with `~/py3env/bin/python -m pip check`; runtime commands
never install packages. A Python upgrade that invalidates this venv requires
recreating it with that interpreter, not switching silently to system Python.

Tests use temporary modules or stateful fixture speakers. They never access
real Sonos devices. Live verification is limited to diagnostics and reading
favorites; actual playback is intentionally not part of automated validation.

The package deployer automatically includes `sonos_cli.py` and its
`sonos_cli_commands.py` argument schema. Install the package
release before the updated Pi wrapper, since general commands need the new CLI.
The existing `ensure_rear_movie` entry remains compatible with pinned video and
frozen rollback releases. Changes to `sonos_tasks.py` affect video's restart
digest; follow `pi/docs/deployment.md` for the scoped package update.

CLI validation covers argument counts, integer volume/increment values (0–100),
source (`optical`/`line`), volume direction, and true boolean flags. For example,
`play_from_faves Album false` preserves grouping. Python-only device objects are
not CLI arguments; `pause`, `stop`, `mute`, `unjoin_all`, and crossfade commands
accept no arguments. `partymode` accepts an optional volume, including zero.
`standby_grouped` is retired because its old grouping behavior interrupts movie
audio. `direct` is Mac-only and propagates AppleScript failure.
