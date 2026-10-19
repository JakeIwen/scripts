// `/clean turns`: the message each turn ended on, for every turn since the
// person's last typed prompt. Worker messages, completion notices and
// scheduled wake-ups each start a turn with its own wrap-up, so the reply the
// person reads last can be a thin status that hides the real report. Pure: no
// engine access, like clean.ts.
import type { SessionMessage } from 'claude-code'

import type { PromptRecord } from '../types'
import { FILTERS, partsOf, type CleanOptions, type Cleaned, type Filter } from './clean'

/** Origins the person produced themselves; every other origin is injected. */
const TYPED_KINDS = new Set(['composer', 'bridge', 'sdk'])

const KIND_LABELS: Record<string, string> = {
  composer: 'your prompt',
  bridge: 'your prompt',
  sdk: 'your prompt',
  'task-notification': 'a background task finishing',
  'scheduled-trigger': 'a scheduled wake-up',
  peer: 'a worker message',
  'peer-send-message': 'a worker message',
  command: 'a slash command',
  interrupt: 'an interruption',
  compaction: 'a context compaction',
}

/** Text markers for prompts that arrived before the mod recorded origins. */
const MARKERS: [RegExp, string][] = [
  [/^\s*<task-notification>/, 'task-notification'],
  [/Another Claude session sent a message|^\s*<agent-message/, 'peer'],
  [/^\s*<command-(name|message)>/, 'command'],
  [/^\s*\[Request interrupted/, 'interrupt'],
  [/^\s*This session is being continued from a previous conversation/, 'compaction'],
]

/** Collapsed whitespace, cut to the length a record keeps. */
export const promptHead = (text: string): string => text.replace(/\s+/g, ' ').trim().slice(0, 160)

/** Where a prompt row came from: a recorded origin first, else text markers, else typed. */
export function promptKind(text: string, records: readonly PromptRecord[]): string {
  const flat = promptHead(text)
  const match = [...records]
    .reverse()
    .find(({ head }) => head !== '' && (flat.startsWith(head) || head.startsWith(flat) || text.includes(head)))
  if (match) return match.kind
  return MARKERS.find(([re]) => re.test(text))?.[1] ?? 'composer'
}

const isPromptRow = (m: SessionMessage): boolean =>
  m.role === 'user' && !(m.toolResults && m.toolResults.length > 0)

type Turn = { kind: string; messages: SessionMessage[] }

/** Splits the transcript after the last typed prompt into turns, one per prompt row. */
export function turnsSinceLastPrompt(
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
): Turn[] {
  const rows = messages.map(m => ({ m, kind: isPromptRow(m) ? promptKind(m.text, records) : '' }))
  const start = rows.findLastIndex(row => TYPED_KINDS.has(row.kind))
  const turns: Turn[] = []
  for (const { m, kind } of rows.slice(Math.max(start, 0))) {
    const current = turns.at(-1)
    if (kind !== '') turns.push({ kind, messages: [] })
    else if (current === undefined) turns.push({ kind: 'composer', messages: [m] })
    else current.messages.push(m)
  }
  return turns.filter(t => t.messages.some(m => m.role === 'assistant'))
}

/** The last text the turn's reply shows once the filters have run. */
function endingText(turn: Turn, opts: CleanOptions, filters: readonly Filter[]): string {
  const assistant = turn.messages.filter(m => m.role === 'assistant')
  const parts = filters.reduce((acc, f) => f.apply(acc, opts), partsOf(assistant))
  const texts = parts.filter(p => p.kind === 'text')
  return texts.at(-1)?.text ?? '_(no text: the turn ended on a tool call)_'
}

export function turnEndings(
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
  opts: CleanOptions,
  filters: readonly Filter[] = FILTERS,
): Cleaned | undefined {
  const turns = turnsSinceLastPrompt(messages, records)
  if (turns.length === 0) return undefined
  const sections = turns.map((turn, i) => {
    const after = KIND_LABELS[turn.kind] ?? turn.kind
    return `### Turn ${i + 1} of ${turns.length} · after ${after}\n\n${endingText(turn, opts, filters)}`
  })
  const label = `turn endings since your last prompt (${turns.length})`
  return { text: sections.join('\n\n---\n\n'), label }
}
