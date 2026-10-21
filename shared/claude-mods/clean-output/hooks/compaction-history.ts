// What a compaction ran over, kept so the views can show the exchanges
// before it: `$.session.messages()` starts at the last compaction's summary. history-store.ts saves and loads the chunks; this
// module only shapes them. Pure: no engine access.
import type { SessionMessage } from 'claude-code'

import type { PromptRecord } from '../types'

/**
 * The rows one compaction ran over, slimmed; how many of the newest it kept
 * (the transcript after it repeats them, right after the summary); and the
 * prompt records known then.
 */
export type HistoryChunk = { rows: SessionMessage[]; kept: number; records: PromptRecord[] }

/** Long enough for the pane's prompt (3,000 characters) after its reminders are stripped. */
const MAX_USER_CHARS = 8000
/** Tool inputs keep what a one-line summary can show. */
const MAX_INPUT_CHARS = 200

const sameRow = (a: SessionMessage, b: SessionMessage): boolean =>
  a.role === b.role && slimText(a) === slimText(b)

/** How many of the newest rows of `before` the compaction kept: `after` is its summary, then those. */
export function keptCount(before: readonly SessionMessage[], after: readonly SessionMessage[]): number {
  let kept = 0
  while (
    kept < before.length &&
    kept < after.length - 1 &&
    sameRow(before[before.length - 1 - kept]!, after[after.length - 1 - kept]!)
  ) kept++
  return kept
}

const slimInput = (input: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(input)
      .map(([key, value]) => [key, typeof value === 'string' ? value.slice(0, MAX_INPUT_CHARS) : value] as const)
      .filter(([, value]) => typeof value === 'string' || (JSON.stringify(value) ?? '').length <= MAX_INPUT_CHARS),
  )

const slimText = (m: SessionMessage): string => (m.role === 'user' ? m.text.slice(0, MAX_USER_CHARS) : m.text)

/** A row with only what the views read: tool results and long inputs dropped. */
export function slimRow(m: SessionMessage): SessionMessage {
  const text = slimText(m)
  const toolUses = m.toolUses.map(u => ({ tool_use_id: u.tool_use_id, tool: u.tool, input: slimInput(u.input ?? {}) }))
  const slim: SessionMessage = { role: m.role, text, toolUses }
  if (m.toolResults && m.toolResults.length > 0) {
    slim.toolResults = m.toolResults.map(r => ({ tool_use_id: r.tool_use_id, text: '', isError: r.isError }))
  }
  return slim
}

/**
 * `next` after `chunk`, minus the rows the compaction kept: they follow its
 * summary there but already end the chunk. All of `next` when they don't match.
 */
function afterChunk(chunk: HistoryChunk, next: readonly SessionMessage[]): readonly SessionMessage[] {
  const { kept } = chunk
  const repeats = next.slice(1, 1 + kept)
  const tail = chunk.rows.slice(chunk.rows.length - kept)
  const isRepeat = kept > 0 && repeats.length === kept && repeats.every((m, i) => sameRow(m, tail[i]!))
  return isRepeat ? [next[0]!, ...next.slice(1 + kept)] : next
}

/** The whole session in the order it ran: each compaction's rows, its summary, then what followed. */
export function withHistory(
  chunks: readonly HistoryChunk[],
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
): { messages: SessionMessage[]; records: PromptRecord[] } {
  const segments = [...chunks.map(c => c.rows), messages]
  const out = [...(segments[0] ?? [])]
  chunks.forEach((chunk, i) => out.push(...afterChunk(chunk, segments[i + 1] ?? [])))
  return { messages: out, records: [...chunks.flatMap(c => c.records), ...records] }
}
