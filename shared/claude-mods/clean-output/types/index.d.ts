export type CleanView = {
  text: string
  label: string
}

declare module 'claude-code' {
  interface PluginState {
    'clean-output': { view: CleanView }
  }
}
