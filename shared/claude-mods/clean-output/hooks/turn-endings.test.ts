import { test, expect } from 'claude-code/testing'

import { parseArgs } from './clean'
import { promptHead, promptKind } from './prompt-kinds'
import { assistant, toolResult, user } from './test-messages'
import { turnEndings } from './turn-endings'

const WAKE = 'Check on the storage worker; if done, run the full check.'

const session = [
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

const records = [
  { head: promptHead('fix these 10 things +sa'), kind: 'composer' },
  { head: promptHead(WAKE), kind: 'scheduled-trigger' },
]

test('lists each turn ending since the last typed prompt, labelled by what started it', () => {
  const view = turnEndings(session, records, parseArgs('turns'))
  expect(view?.label).toBe('turn endings since your last prompt (4)')
  const bands = view?.sections.map(s => s.prompt) ?? []
  expect(bands).toEqual([
    'Turn 1 of 4 · after your prompt\nfix these 10 things +sa',
    'Turn 2 of 4 · after a worker message',
    'Turn 3 of 4 · after a background task finishing',
    'Turn 4 of 4 · after a scheduled wake-up',
  ])
  expect(view?.sections[0]?.reply).toBe('Three workers are running.')
  expect(view?.sections[2]?.reply.startsWith('All 10 items are done.')).toBe(true)
})

test('without a record, an unmarked wake-up counts as a typed prompt', () => {
  expect(promptKind(WAKE, [])).toBe('composer')
  expect(turnEndings(session, [], parseArgs('turns'))?.label).toBe('turn endings since your last prompt (1)')
})

test('a slash command never becomes the last typed prompt', () => {
  const withCommand = [...session, user('<command-name>/clean</command-name>')]
  expect(turnEndings(withCommand, records, parseArgs('turns'))?.label).toBe('turn endings since your last prompt (4)')
})

test('a compaction summary is not mistaken for a typed prompt', () => {
  const summary = 'This session is being continued from a previous conversation that ran out of context.'
  expect(promptKind(summary, [])).toBe('compaction')
})
