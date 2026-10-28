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

## Session search

`claudesearch`, `codexsearch`, and `llmsearch` search newest sessions first.
Ungrouped transcript searches show each session's newest matching excerpt first;
`llmsearch` interleaves both tools by session activity. Indexed results retain
the index's newest-match ordering.

`-g` groups Claude/Codex results per session; `llmsearch -g` combines sessions by
directory and tool. During a grep scan, terminal listings update as sessions are
found and counts accumulate. The final listing is complete; redirected or piped
output contains only that final listing, without terminal control sequences.
Result headers are soft orange (256-colour 215) on terminals. Set `NO_COLOR=1`
to disable colour; pipes, files and JSON records are always plain.

`-t` / `--title` searches only saved session titles for a literal,
case-insensitive term, for example `llmsearch -t "backup"`. It returns one hit
per session and works with `-g`. Title searches read the session stores directly
without using the transcript index; untitled sessions do not match.

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
