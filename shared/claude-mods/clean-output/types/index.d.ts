export type CleanView = {
  text: string
  label: string
}

/** One submitted prompt as `prompt.submit` saw it: a short head of its text and where it came from. */
export type PromptRecord = {
  head: string
  kind: string
}

declare module 'claude-code' {
  interface PluginState {
    'clean-output': { view: CleanView; prompts: PromptRecord[] }
  }
}
