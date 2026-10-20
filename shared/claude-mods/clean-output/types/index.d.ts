/** What `/clean`'s words ask for (parsed by hooks/clean.ts `parseArgs`). */
export type CleanOptions = {
  /** How many exchanges to show, newest last: `all` (the default) or the last N. */
  count: number | 'all'
  /** Copy to the clipboard instead of opening the pane. */
  copy: boolean
  /** Copy markdown (prompts quoted, `---` between exchanges) instead of the plain text the pane shows. */
  markdown: boolean
  /** Keep one-line tool summaries instead of dropping tool calls. */
  tools: boolean
  /** Show each turn's ending since the last typed prompt (turn-endings.ts). */
  turns: boolean
}

/** One block of the pane: a prompt band and the cleaned reply under it. */
export type CleanSection = {
  /** Drawn on the theme's user-message background; quoted in copies. */
  prompt: string
  /** Markdown. */
  reply: string
  /**
   * Where the reply's last turn starts (its divider line), as an offset into
   * `reply`; absent when the reply is one turn. The pane opens there.
   */
  lastTurnAt?: number
}

export type CleanView = {
  label: string
  sections: CleanSection[]
}

/** One submitted prompt as `prompt.submit` saw it: a short head of its text and where it came from. */
export type PromptRecord = {
  head: string
  kind: string
}

/** The pane body's window over its content, as the last `ui.scroll` reported it. */
export type PaneWindow = {
  offset: number
  bodyRows: number
  contentRows: number
}

declare module 'claude-code' {
  interface PluginState {
    'clean-output': {
      pane: CleanView
      /** What the open pane was built from, so a finished turn can rebuild it; null once it closes. */
      options: CleanOptions | null
      prompts: PromptRecord[]
      window: PaneWindow
    }
  }
}
