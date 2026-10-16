# clean-output (Claude Code mod)

`/clean` opens a pane with the last reply's text only: every tool call
(`Bash(...)`, `Read`, `Grep`...) and tool result is stripped. Arrows scroll,
`c` copies the whole text to the clipboard, Esc closes.

- `/clean 2` shows the reply before the last one (any N).
- `/clean copy` copies the clean text without opening the pane.
- `/clean tools` keeps one-line tool summaries (`> Bash cmd...`).

The pane text is also mouse-selectable like any other terminal text.

## Adding filters

`hooks/clean.ts` runs the reply's parts through `FILTERS` in order. Each
filter is `{ name, apply(parts, opts) }` over `Part` values
(`{ kind: 'text' | 'tool', text }`). Append one to the array; the pane picks
it up on the next `/reload-plugins`. `cleanTurn` has no engine dependency, so
the same file can read a Codex rollout once a loader for that format exists.

## Load it

`~/.claude/settings.json` `env.CLAUDE_CODE_PLUGIN_DIRS` names this folder, so
every interactive session loads it. Check with `claude plugin validate .` and
`claude plugin test .` from this folder.
