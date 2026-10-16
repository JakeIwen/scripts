import { test, expect } from 'claude-code/testing'
import type { SessionMessage } from 'claude-code'

import { cleanTurn, parseArgs, replies } from './clean'

const user = (text: string): SessionMessage => ({ role: 'user', text, toolUses: [] })
const toolResult = (): SessionMessage => ({
  role: 'user',
  text: '',
  toolUses: [],
  toolResults: [{ tool_use_id: 't1', text: 'out', isError: false }],
})
const assistant = (text: string, command?: string): SessionMessage => ({
  role: 'assistant',
  text,
  toolUses: command ? [{ tool_use_id: 't1', tool: 'Bash', input: { command } }] : [],
})

const session: SessionMessage[] = [
  user('first question'),
  assistant('Checking.', 'ls ~'),
  toolResult(),
  assistant('First answer.'),
  user('second question'),
  assistant('', 'grep -rn spotlight ~/.zshrc'),
  toolResult(),
  assistant('Looking deeper.\n\n\n\nStill looking.', 'find . -name node_modules'),
  toolResult(),
  assistant('Second answer.\n\n<context>hidden note</context>'),
]

test('groups assistant messages into one reply per prompt', () => {
  expect(replies(session).length).toBe(2)
})

test('the last reply keeps the text and drops the tool calls', () => {
  const cleaned = cleanTurn(session, parseArgs(''))
  expect(cleaned?.text).toBe('Looking deeper.\n\nStill looking.\n\nSecond answer.')
})

test('N reaches earlier replies and "tools" keeps one-line summaries', () => {
  expect(cleanTurn(session, parseArgs('2'))?.text).toBe('Checking.\n\nFirst answer.')
  const withTools = cleanTurn(session, parseArgs('tools'))?.text ?? ''
  expect(withTools.includes('> `Bash` grep -rn spotlight ~/.zshrc')).toBe(true)
  expect(withTools.includes('> `Bash` find . -name node_modules')).toBe(true)
})

test('the pane draws a hint before /clean has run', async $ => {
  const ui = await $.ui.mount({
    plugin: 'clean-output',
    surface: 'terminal',
    component: 'Pane',
    requestId: 'clean',
    props: {
      title: 'Clean',
      isFocused: true,
      bodyColumns: 80,
      placement: 'inline',
      scroll: { offset: 0, bodyRows: 20 },
      view: {},
    },
  })
  expect(await ui.find({ type: 'Markdown' })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /copy/ })).toBeDefined()
})
