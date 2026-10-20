import { atom, read, update } from 'claude-code'
import type { EngineInterface, On, Register } from 'claude-code'

import type { SessionMessage } from 'claude-code'

import type { CleanView, PromptRecord } from '../types'
import { parseArgs, type CleanOptions } from './clean'
import { NO_WINDOW, drawable, lastTurnKey, newestTarget, sectionKey, viewText, windowLine } from './clean-view'
import { cleanExchanges } from './exchanges'
import { TYPED_KINDS, promptHead } from './prompt-kinds'
import { turnEndings } from './turn-endings'

const PANE = 'clean'
/** Asks for the keyboard, so Esc closes the pane before it reaches a running turn. */
const PANE_OPEN = { id: PANE, title: 'Clean', focus: true, closeOnEscape: true, rows: 40 } as const
/** The theme key Claude Code draws behind the person's own prompts. */
const PROMPT_BACKGROUND = 'userMessageBackground'
const EMPTY: CleanView = { label: '', sections: [] }
const NO_PROMPTS: PromptRecord[] = []
const MAX_PROMPTS = 200
const pane = atom({ plugin: 'clean-output', key: 'pane' } as const, EMPTY)
const prompts = atom({ plugin: 'clean-output', key: 'prompts' } as const, NO_PROMPTS)
const paneWindow = atom({ plugin: 'clean-output', key: 'window' } as const, NO_WINDOW)
const NO_OPTIONS: CleanOptions | null = null
const paneOptions = atom({ plugin: 'clean-output', key: 'options' } as const, NO_OPTIONS)
/** Waits before each try: the pane draws its tree a moment after `$.ui.open` places it. */
const REVEAL_WAITS_MS = [0, 50, 100, 200, 400]

/**
 * Scrolls the pane to `target` (or its top, which still reports the window's
 * rows to the band), retrying until the tree is drawn; the end if it never is.
 */
async function revealNewest($: EngineInterface, target: string | undefined): Promise<void> {
  for (const wait of REVEAL_WAITS_MS) {
    if (wait > 0) await $.clock.sleep(wait)
    const to = target === undefined ? 'start' : { key: target }
    const moved = await $.ui.scroll({ in: PANE, to, block: 'start' })
    if (moved.deny === undefined) return
  }
  if (target !== undefined) await $.ui.scroll({ in: PANE, to: 'end' })
}

const buildView = (
  messages: readonly SessionMessage[],
  records: readonly PromptRecord[],
  opts: CleanOptions,
): CleanView | undefined => (opts.turns ? turnEndings(messages, records, opts) : cleanExchanges(messages, records, opts))

/**
 * Typing a prompt hands the keyboard to the composer, and while the turn runs
 * Esc there interrupts it. Taking the keys back lets Esc close an open pane
 * first; the next keystroke the person types returns them.
 */
async function refocusPane($: EngineInterface): Promise<void> {
  if ((await $.ui.panes()).some(p => p.id === PANE)) await $.ui.open(PANE_OPEN)
}

/**
 * Rebuilds an open pane from the options it was opened with and moves to the
 * newest reply, so a pane left open keeps up with the session.
 */
async function refreshPane($: EngineInterface): Promise<void> {
  const opts = await read($, paneOptions)
  if (opts === null || !(await $.ui.panes()).some(p => p.id === PANE)) return
  const view = buildView(await $.session.messages(), await read($, prompts), opts)
  if (view === undefined || JSON.stringify(view) === JSON.stringify(await read($, pane))) return
  await update($, pane, () => view)
  await revealNewest($, newestTarget(view))
}

/** `/clean` itself, and the record of where each prompt came from. */
function onCommand(on: On): void {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'clean',
      description: 'Show the exchanges without tool calls. Args: N (last N), turns, copy, md, tools',
      // Typed mid-turn it opens at once instead of queueing behind the turn.
      immediate: true,
    })
    return next(e)
  })

  // Remember where each prompt came from, so the views can tell the person's
  // own prompts from worker messages, notices and wake-ups.
  on('prompt.submit', async ($, e, next) => {
    if (!e.command) {
      const record: PromptRecord = { head: promptHead(e.text), kind: e.origin.kind }
      // A failed record only weakens the grouping; it must never hold up the prompt.
      await update($, prompts, list => [...list, record].slice(-MAX_PROMPTS)).catch(() => undefined)
    }
    const submitted = await next(e)
    if (!e.command && TYPED_KINDS.has(e.origin.kind)) await refocusPane($).catch(() => undefined)
    return submitted
  })

  on('command.run', { command: 'clean' }, async ($, e) => {
    const opts = parseArgs(e.args)
    const messages = await $.session.messages()
    const view = buildView(messages, await read($, prompts), opts)
    if (view === undefined) return { text: 'clean: no reply found yet.' }
    if (opts.copy) {
      const copied = await $.ui.copy({ text: viewText(view, opts) })
      return { text: copied.isCopied ? `clean: copied the ${view.label}.` : `clean: copy failed (${copied.reason}).` }
    }
    await update($, pane, () => view)
    await update($, paneOptions, () => opts)
    await update($, paneWindow, () => NO_WINDOW)
    const opened = await $.ui.open(PANE_OPEN)
    if (!opened.isPlaced) return { text: `clean: pane not placed (${opened.reason}).` }
    // The newest reply is what the person came for; a pane left at the top
    // hides it below the fold. A failed scroll only leaves it at the top.
    await revealNewest($, newestTarget(view)).catch(() => undefined)
    return {}
  })
}

/** Keeps an open pane's window, content and keyboard in step with the session. */
function onPaneLife(on: On): void {
  // Every move reports the window's rows; the band below draws them, since the
  // pane's own header scrolls away with the rest of its body.
  on('ui.scroll', { requestId: PANE }, async ($, e, next) => {
    const moved = await next(e)
    if (e.component === 'Pane' && moved.deny === undefined) {
      const window = { offset: e.offset, bodyRows: e.bodyRows, contentRows: e.contentRows }
      await update($, paneWindow, () => window).catch(() => undefined)
    }
    return moved
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    // A refresh that fails leaves the pane as it was; the turn is unaffected.
    await refreshPane($).catch(() => undefined)
    return done
  })

  on('ui.close', { id: PANE }, async ($, e, next) => {
    const closed = await next(e)
    await update($, paneWindow, () => NO_WINDOW).catch(() => undefined)
    await update($, paneOptions, () => null).catch(() => undefined)
    return closed
  })
}

/** The rows line in the band above the prompt, while the pane overflows. */
function onBand(on: On): void {
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const line = windowLine(await read($, paneWindow))
    if (line === undefined) return next(e)
    const isShown = (await $.ui.panes()).some(p => p.id === PANE && p.isShown)
    if (!isShown) return next(e)
    const { Text } = $.ui.resolve(e)
    return <Text dimColor>{line}</Text>
  })
}

/** The pane: a header row, then each section's prompt band and its reply. */
function onPane(on: On): void {
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Markdown, Button } = $.ui.resolve(e)
    const current = await read($, pane)
    const markdown = (await read($, paneOptions))?.markdown ?? false
    const { shown, hidden } = drawable(current.sections)
    const note = hidden > 0 ? ` · ${hidden} older not drawn (c copies all)` : ''
    return (
      <Box flexDirection="column">
        <Box>
          <Text dimColor>{(current.label || 'run /clean') + note} · </Text>
          <Button hotkey="c" plain onPress={() => void $.ui.copy({ text: viewText(current, { markdown }) })}>
            copy
          </Button>
          <Text dimColor> · esc closes</Text>
        </Box>
        {shown.length === 0 && <Markdown text="_Nothing to show yet._" />}
        {shown.map((section, i) => {
          const index = hidden + i
          const split = section.lastTurnAt
          return (
            <Box key={sectionKey(index)} flexDirection="column" marginTop={1}>
              <Box backgroundColor={PROMPT_BACKGROUND} paddingX={1}>
                <Text>{section.prompt}</Text>
              </Box>
              {split === undefined ? (
                <Markdown text={section.reply || '_(only tool calls so far)_'} />
              ) : (
                <Box flexDirection="column">
                  <Markdown text={section.reply.slice(0, split).trimEnd()} />
                  <Box key={lastTurnKey(index)} marginTop={1}>
                    <Markdown text={section.reply.slice(split)} />
                  </Box>
                </Box>
              )}
            </Box>
          )
        })}
      </Box>
    )
  })
}

export const register: Register = on => {
  onCommand(on)
  onPaneLife(on)
  onBand(on)
  onPane(on)
}
