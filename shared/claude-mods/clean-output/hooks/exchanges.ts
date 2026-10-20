// The exchange view behind `/clean`, `/clean N` and `/clean all`: each of the
// person's prompts (or slash commands) with everything the assistant wrote
// until the next one, tool calls stripped. Worker messages, notices and
// wake-ups that land mid-exchange show as one dim divider line, not as a new
// exchange. Pure: no engine access.
import type { SessionMessage } from 'claude-code'

import type { CleanSection, CleanView, PromptRecord } from '../types'
import { FILTERS, partsOf, type CleanOptions, type Filter, type Part } from './clean'
import {
  ASSISTANT_KIND,
  COMMAND_KIND,
  SILENT_KINDS,
  TYPED_KINDS,
  classifyRows,
  kindLabel,
  promptDisplay,
} from './prompt-kinds'

export type Exchange = { prompt: string; parts: Part[]; hasReply: boolean }

const divider = (kind: string): Part => ({ kind: 'divider', text: `_after ${kindLabel(kind)}_` })

export function groupExchanges(messages: readonly SessionMessage[], records: readonly PromptRecord[]): Exchange[] {
  const kinds = classifyRows(messages, records)
  const out: Exchange[] = []
  messages.forEach((m, i) => {
    const kind = kinds[i] ?? ASSISTANT_KIND
    const current = out.at(-1)
    if (SILENT_KINDS.has(kind)) return
    if (TYPED_KINDS.has(kind) || kind === COMMAND_KIND) {
      out.push({ prompt: promptDisplay(m.text, kind), parts: [], hasReply: false })
    } else if (kind !== ASSISTANT_KIND) {
      if (current) current.parts.push(divider(kind))
      else out.push({ prompt: `after ${kindLabel(kind)}`, parts: [], hasReply: false })
    } else if (current) {
      current.parts.push(...partsOf([m]))
      current.hasReply = true
    } else {
      out.push({ prompt: '(start of the session)', parts: partsOf([m]), hasReply: true })
    }
  })
  return out.filter(x => x.hasReply)
}

const PART_GAP = '\n\n'

/** The exchange's section: its parts filtered and joined, marking where its last turn starts. */
export function exchangeSection(
  exchange: Exchange,
  opts: CleanOptions,
  filters: readonly Filter[],
): CleanSection {
  const kept = filters.reduce((acc, f) => f.apply(acc, opts), [...exchange.parts])
  const texts = kept.map(p => p.text)
  const reply = texts.join(PART_GAP)
  // A reply that opens on a divider has no earlier turn to skip past.
  const at = kept.findLastIndex(p => p.kind === 'divider')
  if (at <= 0) return { prompt: exchange.prompt, reply }
  return { prompt: exchange.prompt, reply, lastTurnAt: texts.slice(0, at).join(PART_GAP).length + PART_GAP.length }
}

function exchangeLabel(shown: number, total: number, opts: CleanOptions): string {
  if (opts.count === 'all') return total === 1 ? '1 exchange' : `all ${total} exchanges`
  const which = shown === 1 ? 'last exchange' : `last ${shown} exchanges`
  return `${which} (${total} in session)`
}

export function cleanExchanges(
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
  opts: CleanOptions,
  filters: readonly Filter[] = FILTERS,
): CleanView | undefined {
  const all = groupExchanges(messages, records)
  if (all.length === 0) return undefined
  const chosen = opts.count === 'all' ? all : all.slice(-opts.count)
  const sections = chosen.map(x => exchangeSection(x, opts, filters))
  return { label: exchangeLabel(chosen.length, all.length, opts), sections }
}
