# Shared code

This directory is for code used on more than one host. `python/` is added to
the MacBook's `PYTHONPATH`. The Pi's broad sync copies `sh/` executables flat into
`/home/pi/scripts/`. Pi package releases preserve its package path.
Pi shell utilities and the packaged video service include the current release's
`shared/python` for standalone `sonos_tasks` imports; changes to that helper are
part of the video's restart digest. `sns.sh` preserves inherited paths for the
frozen flat video's saved-unit rollback. No current deployer writes the retired
`/home/pi/scripts/python-automation/` tree. The Mac wrapper remains unchanged. See
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

`-t` / `--title` searches only saved session titles for a literal,
case-insensitive term, for example `llmsearch -t "backup"`. It returns one hit
per session and works with `-g`. Title searches read the session stores directly
without using the transcript index; untitled sessions do not match.

The scripts require Bash, grep, jq, and Python 3.9 or newer. Keep all seven
`sh/*search*` files together when deploying them to `/home/pi/scripts/`.
