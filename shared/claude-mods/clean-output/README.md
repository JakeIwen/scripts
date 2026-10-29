# clean-output (Claude Code mod)

`/clean` opens a pane with every exchange in the session: each prompt on the
same tinted background the transcript gives your own messages, then the
reply's text only. Every tool call (`Bash(...)`, `Read`, `Grep`...) and tool
result is stripped. `c` copies the text as the pane shows it, Esc closes.

It runs at once even while Claude is working, instead of queueing behind
the turn. A pane left open rebuilds itself when each turn ends, and takes
the keyboard back after each prompt you send, so Esc closes it before Esc
can interrupt the turn; a second Esc interrupts. Once you start typing the
keys are the prompt's again: ctrl+x tab returns them to the pane.

The pane opens on the newest reply: at the start of its last turn when
injected messages split it, else at the newest prompt. A line under the pane
says which rows show (`/clean: rows 95–131 of 145 · more above and below`),
since the pane's own header scrolls away. ↑↓, PgUp/PgDn and Home/End scroll
while the pane holds the keyboard.

- An exchange is one of your prompts (or a slash command that got a reply)
  and everything the assistant wrote until your next one. Worker messages,
  background-task notices and scheduled wake-ups that land in between stay
  in the same exchange as one dim line (`after a worker message`).
- `/clean 1` shows only your last exchange; `/clean 3` the last 3, oldest
  first (any N).
- One drawing holds about 90,000 characters, so in a long session the oldest
  exchanges are left out of the pane; the pane's header says how many, and
  `c` still copies all of them.
- `/clean copy` copies the text without opening the pane. Copies are plain
  text, as the pane shows it: each prompt marked `❯` as the transcript marks
  your messages, the reply without markdown marks (no `**`, backticks or
  fences; links as `text (url)`; tables aligned).
- `md` copies markdown instead, for the pane's `c` and for `copy`: each
  prompt quoted (`> ...`) above its reply, `---` between exchanges.
- `/clean tools` keeps one-line tool summaries (`> Bash cmd...`).
- `/clean turns` shows the message each turn ended on, for every turn since
  your last typed prompt, labelled by what started it (your prompt, a worker
  message, a background task finishing, a scheduled wake-up). Worker
  messages and wake-ups each end a turn with their own wrap-up, so the reply
  you see last can be a thin status that hides the real report a few turns
  earlier.
- Words combine in any order: `/clean 3 tools copy md`.

Compaction doesn't hide earlier exchanges: when the conversation compacts,
the mod saves the rows it ran over (text and one-line tool inputs, no tool
output) to `~/.claude/clean-output/history/<session id>/`, one file per
compaction, and `/clean` shows them before the live transcript, so a resumed
session keeps them too. Compactions from before the mod saved them, and a
subagent's own, are not covered.

The mod records each prompt's origin as it is submitted, so grouping is exact
from the time it loaded. Older prompts fall back to text markers (task
notifications, worker messages, slash commands, compaction summaries); a
scheduled wake-up from before the mod loaded counts as one of your prompts.

The pane text is also mouse-selectable like any other terminal text.

## Install

Claude Code loads the mod from the folder named in `settings.json`
`env.CLAUDE_CODE_PLUGIN_DIRS`: this checkout on the Mac. The Pi has no
checkout, so `zsh deploy_pi.zsh` copies the tracked files there
(`~/.local/share/claude-mods/clean-output/`) and runs the tests on the Pi's
Claude Code; re-run it after any change. Open sessions pick up a new copy
with `/reload-plugins`.

## Adding filters

`hooks/clean.ts` runs each reply's parts through `FILTERS` in order. Each
filter is `{ name, apply(parts, opts) }` over `Part` values
(`{ kind: 'text' | 'tool' | 'divider', text }`). Append one to the array; the
pane picks it up on the next `/reload-plugins`. The files under `hooks/` have
no engine dependency except `register.tsx` and `history-store.ts`, so `codexclean` (below) reuses
them for Codex sessions:

- `clean.ts`: options, parts and filters.
- `prompt-kinds.ts`: where each prompt row came from (typed, slash command,
  worker message, notice, wake-up), shared by both views.
- `exchanges.ts`: the exchange view (`/clean`, `/clean N`).
- `turn-endings.ts`: the `turns` view.
- `compaction-history.ts`: the rows a compaction ran over, slimmed, and the
  session stitched back together around each summary
  (`history-store.ts`, engine-side, saves and loads them).
- `clean-view.ts`: the pane's sections, the copy text, the drawing budget,
  where the pane opens, and the status line.
- `markdown.ts`: reply markdown as plain text (copies) or an HTML fragment
  (codexclean's page).
- `codex-rollout.ts`: a Codex session file as rows and prompt records.

## codexclean (Codex)

The same view for a Codex session, as a shell command on the Mac and the Pi:

    codexclean [N] [turns] [tools] [md] [copy | open | watch | print] [--session <id|file>]

It reads `~/.codex/sessions/**/rollout-*.jsonl`: the `--session` id or file,
else the session it runs in (`$CODEX_THREAD_ID`, set for Codex's `!`
commands), else the newest session started in the current folder. Codex logs
each typed prompt and each turn's trigger, so grouping needs no guessing: a
goal continuation or compaction is one divider line, and a `!command` is left
out. Files from before Codex logged items (about 2026-09) read from their
message events.

- No mode prints to the terminal, paged with `less` (opening at the end)
  when longer than a screen, each prompt on a shaded band.
- `copy` copies the plain text (`md` for markdown) and prints one line.
- `open` saves the view as a page in `~/.agent/artifacts/` with
  `agent_art.py`, one page per session rewritten each time, and opens it on
  the Mac; the Pi prints the page's path under the Mac's mount.
- `watch` redraws the newest exchange (or the last N) as Codex writes it.
  Run it in its own pane: `codexclean watch --session <id>`.
- Inside Codex, a `!command`'s output is recorded as a user message, so it
  joins the model's context. A bare `!codexclean` therefore saves and opens
  the page and prints one line; `!codexclean print` prints anyway, and
  `!codexclean watch` prints the split-pane command with this session's id.

`codex/build.mjs` strips the TypeScript types from the modules above
(Node >= 22.13) and lays them out with `codex/codexclean.mjs` as plain ES
modules, which run on Node 20 (the Pi's). `zsh codex/deploy.zsh` builds and
installs that on the Pi, then the Mac: `~/.local/share/codexclean/` and a
launcher at `~/.local/bin/codexclean`. The installed copies don't follow the
checkout: re-run it after editing any of those modules. `codex/codexclean.test.mjs`
(`node --test`) builds into a temp folder and runs the CLI against a
synthetic Codex home.

## Load it

`~/.claude/settings.json` `env.CLAUDE_CODE_PLUGIN_DIRS` names this folder, so
every interactive session loads it. Check with `claude plugin validate .` and
`claude plugin test .` from this folder.
