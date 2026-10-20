// What the pane shows and `copy` copies: sections of a prompt band plus the
// cleaned reply under it. Pure helpers over CleanView.
import type { CleanSection, CleanView, PaneWindow } from '../types'
import { markdownToPlain } from './markdown'

/** One drawing holds 100,000 characters of markdown in all; keep headroom for the bands. */
export const DRAW_BUDGET = 90_000

const quote = (text: string): string =>
  text
    .split('\n')
    .map(line => (line === '' ? '>' : `> ${line}`))
    .join('\n')

/** A prompt as the transcript marks one: `❯` on its first line, the rest indented under it. */
const marked = (text: string): string =>
  text
    .split('\n')
    .map((line, i) => (i === 0 ? `❯ ${line}` : line === '' ? '' : `  ${line}`))
    .join('\n')

/**
 * The whole view as text: by default plain, as the pane shows it (each prompt
 * marked `❯`, the reply without markdown marks); with `markdown`, each prompt
 * quoted above its reply and `---` between exchanges.
 */
export function viewText(view: CleanView, { markdown = false }: { markdown?: boolean } = {}): string {
  if (markdown) return view.sections.map(s => `${quote(s.prompt)}\n\n${s.reply}`).join('\n\n---\n\n')
  return view.sections.map(s => `${marked(s.prompt)}\n${markdownToPlain(s.reply)}`).join('\n\n')
}

/** The newest sections that fit one drawing, and how many older ones were left out. */
export function drawable(sections: readonly CleanSection[], budget = DRAW_BUDGET): {
  shown: CleanSection[]
  hidden: number
} {
  const shown: CleanSection[] = []
  let used = 0
  for (const section of [...sections].reverse()) {
    const size = section.prompt.length + section.reply.length
    if (shown.length > 0 && used + size > budget) break
    shown.unshift(section)
    used += size
  }
  return { shown, hidden: sections.length - shown.length }
}

/** The element key of a section's box, by its index in the view. */
export const sectionKey = (index: number): string => `section-${index}`
/** The element key of the box holding a section's last turn. */
export const lastTurnKey = (index: number): string => `last-turn-${index}`

/**
 * Where the pane opens: the start of the newest section's last turn, else of
 * the newest section; none when that is the only section, already at the top.
 */
export function newestTarget(view: CleanView): string | undefined {
  const last = view.sections.length - 1
  const newest = view.sections[last]
  if (newest === undefined) return undefined
  if (newest.lastTurnAt !== undefined) return lastTurnKey(last)
  return last > 0 ? sectionKey(last) : undefined
}

export const NO_WINDOW: PaneWindow = { offset: 0, bodyRows: 0, contentRows: 0 }

/** The status line under the pane: which rows show, and which way more lies. None when all fit. */
export function windowLine({ offset, bodyRows, contentRows }: PaneWindow): string | undefined {
  if (bodyRows <= 0 || contentRows <= bodyRows) return undefined
  const last = Math.min(offset + bodyRows, contentRows)
  const isAbove = offset > 0
  const isBelow = last < contentRows
  const more =
    isAbove && isBelow ? 'more above and below (↑↓ PgUp PgDn)' : isBelow ? 'more below (↓ PgDn End)' : 'more above (↑ PgUp Home)'
  return `/clean: rows ${offset + 1}–${last} of ${contentRows} · ${more}`
}
