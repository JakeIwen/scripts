import { test, expect } from 'claude-code/testing'

import { parseArgs } from './clean'
import { viewText } from './clean-view'
import { readRollout } from './codex-rollout'
import { cleanExchanges } from './exchanges'
import { turnEndings } from './turn-endings'

const line = (type: string, payload: object): string => JSON.stringify({ type, payload })
const turn = (trigger: string | null): string =>
  line('event_msg', { type: 'task_started', turn_attribution: { turn_trigger: trigger } })
const item = (value: object): string => line('event_msg', { type: 'item_completed', item: value })
const said = (text: string): string => item({ type: 'AgentMessage', content: [{ type: 'Text', text }], phase: 'commentary' })
const typed = (text: string): string => item({ type: 'UserMessage', content: [{ type: 'text', text }] })
const ran = (cmd: string, source = 'unified_exec_startup'): string =>
  item({ type: 'CommandExecution', id: 'c1', command: ['/bin/zsh', '-lc', cmd], parsed_cmd: [{ cmd }], source })

const session = [
  line('session_meta', { id: 'abc', cwd: '/w', thread_source: 'user' }),
  turn('user'),
  typed('fix the build'),
  said('Checking the logs.'),
  ran('npm test'),
  said('Fixed: the **lockfile** was stale.'),
  turn(null),
  ran('codexclean copy', 'user_shell'),
  turn('goal'),
  said('Goal check: still green.'),
  'not json, a line still being written',
].join('\n')

test('a Codex session reads as exchanges: tool calls dropped, a goal turn as one divider', () => {
  const { meta, messages, records } = readRollout(session)
  expect(meta).toEqual({ id: 'abc', cwd: '/w', threadSource: 'user' })
  const view = cleanExchanges(messages, records, parseArgs(''))
  expect(view?.sections.map(s => s.prompt)).toEqual(['fix the build'])
  expect(view?.sections[0]?.reply).toBe(
    'Checking the logs.\n\nFixed: the **lockfile** was stale.\n\n_after a goal continuation_\n\nGoal check: still green.',
  )
  expect(view ? viewText(view) : '').toContain('Fixed: the lockfile was stale.')
})

test('a !command is silent: turns still reach back to the last real prompt', () => {
  const { messages, records } = readRollout(session)
  const view = turnEndings(messages, records, parseArgs('turns'))
  expect(view?.sections.map(s => s.reply)).toEqual(['Fixed: the **lockfile** was stale.', 'Goal check: still green.'])
})

test('files from before Codex logged items read from their message events', () => {
  const legacy = [
    line('session_meta', { id: 'old', cwd: '/w' }),
    line('event_msg', { type: 'task_started' }),
    line('event_msg', { type: 'user_message', message: 'build a dashboard' }),
    line('event_msg', { type: 'agent_message', message: 'Mapping the server.' }),
    line('event_msg', { type: 'exec_command_end', call_id: 'x', command: ['/bin/zsh', '-lc', 'ls'] }),
    line('event_msg', { type: 'agent_message', message: 'Done.' }),
  ].join('\n')
  const { messages, records } = readRollout(legacy)
  const view = cleanExchanges(messages, records, parseArgs('tools'))
  expect(view?.sections[0]?.prompt).toBe('build a dashboard')
  expect(view?.sections[0]?.reply).toBe('Mapping the server.\n\n> `shell` ls\n\nDone.')
})
