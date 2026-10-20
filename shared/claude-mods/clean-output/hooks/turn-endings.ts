// `/clean turns`: the message each turn ended on, for every turn since the
// person's last typed prompt. Worker messages, completion notices and
// scheduled wake-ups each start a turn with its own wrap-up, so the reply the
// person reads last can be a thin status that hides the real report. Pure: no
// engine access, like clean.ts.
import type { SessionMessage } from 'claude-code'

import type { CleanView, PromptRecord } from '../types'
import { FILTERS, partsOf, type CleanOptions, type Filter } from './clean'
import { ASSISTANT_KIND, SILENT_KINDS, TYPED_KINDS, classifyRows, kindLabel, promptDisplay } from './prompt-kinds'

type Turn = { kind: string; prompt: string; messages: SessionMessage[] }

/** Splits the transcript after the last typed prompt into turns, one per prompt row. */
export function turnsSinceLastPrompt(
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
): Turn[] {
  const kinds = classifyRows(messages, records)
  const start = kinds.findLastIndex(kind => TYPED_KINDS.has(kind))
  const turns: Turn[] = []
  messages.forEach((m, i) => {
    const kind = kinds[i] ?? ASSISTANT_KIND
    const current = turns.at(-1)
    if (i < start || SILENT_KINDS.has(kind)) return
    if (kind !== ASSISTANT_KIND) turns.push({ kind, prompt: promptDisplay(m.text, kind), messages: [] })
    else if (current === undefined) turns.push({ kind: 'composer', prompt: '', messages: [m] })
    else current.messages.push(m)
  })
  return turns.filter(t => t.messages.some(m => m.role === 'assistant'))
}

/** The last text the turn's reply shows once the filters have run. */
function endingText(turn: Turn, opts: CleanOptions, filters: readonly Filter[]): string {
  const parts = filters.reduce((acc, f) => f.apply(acc, opts), partsOf(turn.messages))
  const texts = parts.filter(p => p.kind === 'text')
  return texts.at(-1)?.text ?? '_(no text: the turn ended on a tool call)_'
}

function bandText(turn: Turn, index: number, total: number): string {
  const head = `Turn ${index + 1} of ${total} · after ${kindLabel(turn.kind)}`
  return TYPED_KINDS.has(turn.kind) && turn.prompt !== '' ? `${head}\n${turn.prompt}` : head
}

export function turnEndings(
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
  opts: CleanOptions,
  filters: readonly Filter[] = FILTERS,
): CleanView | undefined {
  const turns = turnsSinceLastPrompt(messages, records)
  if (turns.length === 0) return undefined
  const sections = turns.map((turn, i) => ({
    prompt: bandText(turn, i, turns.length),
    reply: endingText(turn, opts, filters),
  }))
  return { label: `turn endings since your last prompt (${turns.length})`, sections }
}
