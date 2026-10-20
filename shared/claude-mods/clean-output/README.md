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

The mod records each prompt's origin as it is submitted, so grouping is exact
from the time it loaded. Older prompts fall back to text markers (task
notifications, worker messages, slash commands, compaction summaries); a
scheduled wake-up from before the mod loaded counts as one of your prompts.

The pane text is also mouse-selectable like any other terminal text.

## Adding filters

`hooks/clean.ts` runs each reply's parts through `FILTERS` in order. Each
filter is `{ name, apply(parts, opts) }` over `Part` values
(`{ kind: 'text' | 'tool' | 'divider', text }`). Append one to the array; the
pane picks it up on the next `/reload-plugins`. The files under `hooks/` have
no engine dependency except `register.tsx`, so the same code can read a Codex
rollout once a loader for that format exists:

- `clean.ts`: options, parts and filters.
- `prompt-kinds.ts`: where each prompt row came from (typed, slash command,
  worker message, notice, wake-up), shared by both views.
- `exchanges.ts`: the exchange view (`/clean`, `/clean N`).
- `turn-endings.ts`: the `turns` view.
- `clean-view.ts`: the pane's sections, the copy text, the drawing budget,
  where the pane opens, and the status line.
- `markdown.ts`: reply markdown as plain text (copies) or an HTML fragment
  (codexclean's page).

## Load it

`~/.claude/settings.json` `env.CLAUDE_CODE_PLUGIN_DIRS` names this folder, so
every interactive session loads it. Check with `claude plugin validate .` and
`claude plugin test .` from this folder.
