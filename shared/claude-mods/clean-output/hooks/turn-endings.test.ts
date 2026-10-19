import { test, expect } from 'claude-code/testing'
import type { SessionMessage } from 'claude-code'

import { parseArgs } from './clean'
import { promptHead, promptKind, turnEndings } from './turn-endings'

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

const WAKE = 'Check on the storage worker; if done, run the full check.'

const session: SessionMessage[] = [
  user('old question'),
  assistant('Old answer.'),
  user('fix these 10 things +sa'),
  assistant('Launching workers.', 'ls'),
  toolResult(),
  assistant('Three workers are running.'),
  user('Another Claude session sent a message:\n<agent-message from="a1">progress</agent-message>'),
  assistant('The editor worker is halfway.'),
  user('<task-notification>\n<status>completed</status>\n</task-notification>'),
  assistant('Running the full check.', 'npm run check'),
  toolResult(),
  assistant('All 10 items are done.\n\n**Decision for you:** keep saved rows?'),
  user(WAKE),
  assistant('Nothing new. Waiting on you: say deploy.'),
]

test('lists each turn ending since the last typed prompt, labelled by what started it', () => {
  const records = [{ head: promptHead(WAKE), kind: 'scheduled-trigger' }]
  const out = turnEndings(session, records, parseArgs('turns'))
  expect(out?.label).toBe('turn endings since your last prompt (4)')
  const text = out?.text ?? ''
  expect(text.includes('Old answer.')).toBe(false)
  expect(text.includes('Launching workers.')).toBe(false)
  expect(text.includes('### Turn 1 of 4 · after your prompt\n\nThree workers are running.')).toBe(true)
  expect(text.includes('after a worker message\n\nThe editor worker is halfway.')).toBe(true)
  expect(text.includes('after a background task finishing\n\nAll 10 items are done.')).toBe(true)
  expect(text.includes('### Turn 4 of 4 · after a scheduled wake-up')).toBe(true)
})

test('without a record, an unmarked wake-up counts as a typed prompt', () => {
  expect(promptKind(WAKE, [])).toBe('composer')
  expect(turnEndings(session, [], parseArgs('turns'))?.label).toBe('turn endings since your last prompt (1)')
})

test('a recorded slash command never becomes the last typed prompt', () => {
  const withCommand = [...session, user('<command-name>/clean</command-name>')]
  const records = [{ head: promptHead(WAKE), kind: 'scheduled-trigger' }]
  expect(turnEndings(withCommand, records, parseArgs('turns'))?.label).toBe(
    'turn endings since your last prompt (4)',
  )
})

test('a compaction summary is not mistaken for a typed prompt', () => {
  const summary = user('This session is being continued from a previous conversation that ran out of context.')
  expect(promptKind(summary.text, [])).toBe('compaction')
})
