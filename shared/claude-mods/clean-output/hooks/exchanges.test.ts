import { test, expect } from 'claude-code/testing'

import { parseArgs } from './clean'
import { viewText } from './clean-view'
import { cleanExchanges, groupExchanges } from './exchanges'
import { promptHead } from './prompt-kinds'
import { assistant, toolResult, user } from './test-messages'

const session = [
  user('first question'),
  assistant('Checking.', 'ls ~'),
  toolResult(),
  assistant('First answer.'),
  user('<command-name>/clean</command-name>\n<command-message>clean</command-message>\n<command-args></command-args>'),
  user('<local-command-stdout>clean-output: </local-command-stdout>'),
  user('second question<system-reminder>hidden</system-reminder>'),
  assistant('', 'grep -rn spotlight ~/.zshrc'),
  toolResult(),
  assistant('Looking deeper.\n\n\n\nStill looking.', 'find . -name node_modules'),
  toolResult(),
  user('Another Claude session sent a message:\n<agent-message from="a1">progress</agent-message>'),
  assistant('', 'npm test'),
  toolResult(),
  user('<task-notification>\n<status>completed</status>\n</task-notification>'),
  assistant('Second answer.\n\n<context>hidden note</context>'),
  user('<command-name>/clean</command-name>\n<command-args>3</command-args>'),
]

test('one exchange per typed prompt; commands with no reply and their output are dropped', () => {
  const prompts = groupExchanges(session, []).map(x => x.prompt)
  expect(prompts).toEqual(['first question', 'second question'])
})

test('/clean 1 shows the last exchange with its prompt, one divider for the injections', () => {
  const view = cleanExchanges(session, [], parseArgs('1'))
  expect(view?.label).toBe('last exchange (2 in session)')
  expect(view?.sections.length).toBe(1)
  expect(view?.sections[0]?.prompt).toBe('second question')
  expect(view?.sections[0]?.reply).toBe(
    'Looking deeper.\n\nStill looking.\n\n_after a background task finishing_\n\nSecond answer.',
  )
})

test('/clean N shows the last N exchanges, oldest first; bare /clean shows every one', () => {
  const two = cleanExchanges(session, [], parseArgs('5'))
  expect(two?.label).toBe('last 2 exchanges (2 in session)')
  expect(two?.sections.map(s => s.prompt)).toEqual(['first question', 'second question'])
  expect(two?.sections[0]?.reply).toBe('Checking.\n\nFirst answer.')
  expect(cleanExchanges(session, [], parseArgs(''))?.label).toBe('all 2 exchanges')
  expect(cleanExchanges(session.slice(0, 4), [], parseArgs(''))?.label).toBe('1 exchange')
})

test('tools keeps one-line summaries; md copy quotes each prompt', () => {
  const view = cleanExchanges(session, [], parseArgs('tools'))
  const text = view ? viewText(view, { markdown: true }) : ''
  expect(text.includes('> `Bash` grep -rn spotlight ~/.zshrc')).toBe(true)
  expect(text.startsWith('> first question\n\nChecking.')).toBe(true)
  expect(text.includes('\n\n---\n\n> second question\n\n')).toBe(true)
})

test('plain copy marks each prompt as the transcript does and drops the markdown marks', () => {
  const view = cleanExchanges(session, [], parseArgs(''))
  const text = view ? viewText(view) : ''
  expect(text.startsWith('❯ first question\nChecking.\n\nFirst answer.\n\n❯ second question\n')).toBe(true)
  expect(text.includes('\nafter a background task finishing\n')).toBe(true)
})

test('a slash command with a reply is an exchange, shown as typed', () => {
  const rows = [user('<command-name>/code-review</command-name>\n<command-args>high</command-args>'), assistant('Reviewed.')]
  expect(cleanExchanges(rows, [], parseArgs(''))?.sections[0]?.prompt).toBe('/code-review high')
})

test('once origins are recorded, an unmarked injected row joins the exchange instead of opening one', () => {
  const wake = 'Check on the storage worker; if done, run the full check.'
  const rows = [user('fix it'), assistant('Fixed.'), user(wake), assistant('Nothing new.')]
  const records = [{ head: promptHead('fix it'), kind: 'composer' }]
  const view = cleanExchanges(rows, records, parseArgs(''))
  expect(view?.sections[0]?.prompt).toBe('fix it')
  expect(view?.sections[0]?.reply).toBe('Fixed.\n\n_after an injected message_\n\nNothing new.')
  expect(groupExchanges(rows, []).length).toBe(2)
})

test('the last turn is marked where its divider starts, so the pane can open on it', () => {
  const notice = user('<task-notification>\n<status>completed</status>\n</task-notification>')
  const rows = [user('fix it'), assistant('Fixed.'), notice, assistant('All done.')]
  const section = cleanExchanges(rows, [], parseArgs(''))?.sections[0]
  expect(section?.reply.slice(section.lastTurnAt)).toBe('_after a background task finishing_\n\nAll done.')
  expect(cleanExchanges([user('hi'), assistant('Hello.')], [], parseArgs(''))?.sections[0]?.lastTurnAt).toBeUndefined()
})

test('the prompt band drops paste wrapper tags and keeps the pasted text', () => {
  const rows = [user('+sa\n\n<pasted_content id="78ba">\n1. fix the calendar\n</pasted_content id="78ba">'), assistant('Done.')]
  expect(cleanExchanges(rows, [], parseArgs(''))?.sections[0]?.prompt).toBe('+sa\n\n1. fix the calendar')
})
