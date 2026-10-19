# clean-output (Claude Code mod)

`/clean` opens a pane with the last reply's text only: every tool call
(`Bash(...)`, `Read`, `Grep`...) and tool result is stripped. Arrows scroll,
`c` copies the whole text to the clipboard, Esc closes.

- `/clean 2` shows the reply before the last one (any N).
- `/clean copy` copies the clean text without opening the pane.
- `/clean tools` keeps one-line tool summaries (`> Bash cmd...`).
- `/clean turns` shows the message each turn ended on, for every turn since
  your last typed prompt, labelled by what started it (your prompt, a worker
  message, a background task finishing, a scheduled wake-up). Worker
  messages and wake-ups each end a turn with their own wrap-up, so the reply
  you see last can be a thin status that hides the real report a few turns
  earlier. `/clean turns copy` copies the same text.

`/clean turns` learns each prompt's origin as it is submitted, so it is
exact from the time the mod loaded. Older prompts fall back to text markers
(task notifications, worker messages, slash commands, compaction summaries);
a scheduled wake-up from before the mod loaded counts as a typed prompt.

The pane text is also mouse-selectable like any other terminal text.

## Adding filters

`hooks/clean.ts` runs the reply's parts through `FILTERS` in order (`/clean turns`,
in `hooks/turn-endings.ts`, uses the same filters). Each
filter is `{ name, apply(parts, opts) }` over `Part` values
(`{ kind: 'text' | 'tool', text }`). Append one to the array; the pane picks
it up on the next `/reload-plugins`. `cleanTurn` has no engine dependency, so
the same file can read a Codex rollout once a loader for that format exists.

## Load it

`~/.claude/settings.json` `env.CLAUDE_CODE_PLUGIN_DIRS` names this folder, so
every interactive session loads it. Check with `claude plugin validate .` and
`claude plugin test .` from this folder.
