import { test, expect } from 'claude-code/testing'

import { parseArgs } from './clean'
import { keptCount, slimRow, withHistory } from './compaction-history'
import { cleanExchanges } from './exchanges'
import { assistant, toolResult, user } from './test-messages'

const summary = user('This session is being continued from a previous conversation that ran out of context.')

test('counts the newest rows a compaction kept after its summary', () => {
  const before = [user('first'), assistant('One.'), user('second'), assistant('Two.')]
  expect(keptCount(before, [summary])).toBe(0)
  expect(keptCount(before, [summary, user('second'), assistant('Two.')])).toBe(2)
  // The summary itself is never taken for a kept row.
  expect(keptCount([summary], [summary])).toBe(0)
})

test('a slimmed row keeps what the views read and drops tool output and long inputs', () => {
  const big = { tool_use_id: 't1', tool: 'Write', input: { file_path: '/a', content: 'x'.repeat(5000), n: 3 } }
  const slim = slimRow({ role: 'assistant', text: 'Writing.', toolUses: [{ ...big, result: 'ok', text: 'done' }] })
  expect(slim).toEqual({
    role: 'assistant',
    text: 'Writing.',
    toolUses: [{ tool_use_id: 't1', tool: 'Write', input: { file_path: '/a', content: 'x'.repeat(200), n: 3 } }],
  })
  expect(slimRow(toolResult()).toolResults).toEqual([{ tool_use_id: 't1', text: '', isError: false }])
  expect(slimRow(user('y'.repeat(20_000))).text.length).toBe(8000)
})

const before = [user('first question'), assistant('Old answer.', 'ls'), user('second question'), assistant('Kept answer.')]
const chunk = { rows: before.map(slimRow), kept: 1, records: [] }

test('/clean shows the exchanges before a compaction, each reply under its own prompt', () => {
  const live = [summary, assistant('Kept answer.'), assistant('Resumed.'), user('third question'), assistant('New answer.')]
  const { messages, records } = withHistory([chunk], live, [])
  const view = cleanExchanges(messages, records, parseArgs(''))
  expect(view?.sections.map(s => s.prompt)).toEqual(['first question', 'second question', 'third question'])
  expect(view?.sections[1]?.reply).toBe('Kept answer.\n\n_after a context compaction_\n\nResumed.')
})

test('rows that do not repeat the kept ones are all shown', () => {
  const live = [summary, assistant('Something else.')]
  expect(withHistory([chunk], live, []).messages).toEqual([...chunk.rows, ...live])
})

test('chunks from successive compactions chain in order', () => {
  const second = { rows: [summary, assistant('Kept answer.'), user('third question'), assistant('Three.')], kept: 0, records: [] }
  const live = [user('This session is being continued from a previous conversation.'), user('fourth question')]
  const prompts = cleanExchanges(withHistory([chunk, second], live, []).messages, [], parseArgs(''))?.sections.map(s => s.prompt)
  expect(prompts).toEqual(['first question', 'second question', 'third question'])
})
