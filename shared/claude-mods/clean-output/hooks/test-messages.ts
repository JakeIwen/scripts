// Transcript rows for the tests, in the shape `$.session.messages()` returns.
import type { SessionMessage } from 'claude-code'

export const user = (text: string): SessionMessage => ({ role: 'user', text, toolUses: [] })

export const toolResult = (): SessionMessage => ({
  role: 'user',
  text: '',
  toolUses: [],
  toolResults: [{ tool_use_id: 't1', text: 'out', isError: false }],
})

export const assistant = (text: string, command?: string): SessionMessage => ({
  role: 'assistant',
  text,
  toolUses: command ? [{ tool_use_id: 't1', tool: 'Bash', input: { command } }] : [],
})
