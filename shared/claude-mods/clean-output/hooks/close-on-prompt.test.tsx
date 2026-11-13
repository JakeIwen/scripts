import { test, expect } from 'claude-code/testing'
import type { On, PromptSubmitInput, PromptSubmitResult, SessionMessage } from 'claude-code'

import { assistant, user } from './test-messages'

const PANE = 'clean'
const TWO_EXCHANGES = [user('first question'), assistant('First answer.'), user('second question'), assistant('Second answer.')]
const THREE_EXCHANGES = [...TWO_EXCHANGES, user('third question'), assistant('Third answer.')]

type World = {
  /** Ids the mod closed, in order. */
  closed: string[]
  /** How many times the mod asked to open (or refocus) the pane. */
  opens: () => number
  /** What prompts beneath the mod answer; every one enters by default. */
  submit: (e: PromptSubmitInput) => Promise<PromptSubmitResult>
  /** What `$.session.messages()` answers. */
  messages: () => Promise<SessionMessage[]>
  /** The engine drops the pane from its record, as the person's close does. */
  personCloses: () => void
  /** While true, a hook beneath refuses every close and the pane stays. */
  refuseClose: boolean
  /** While true, a hook beneath answers every close as done without passing it on, and the pane stays. */
  answerClose: boolean
  /** The engine lists the pane again without the mod opening it. */
  relist: () => void
  /** Runs inside `ui.panes` after the answer is taken, before it returns. */
  listed: () => Promise<void>
}

/**
 * The engine beneath the mod: a session, a pane record that `ui.open` sets
 * and `ui.close` clears (unless a hook refuses or answers for the close), and
 * prompts answered by `world.submit`.
 */
function world(on: On): World {
  let isOpen = false
  let opens = 0
  const w: World = {
    closed: [],
    opens: () => opens,
    submit: async e => ({ text: e.text }),
    messages: async () => TWO_EXCHANGES,
    personCloses: () => {
      isOpen = false
    },
    refuseClose: false,
    answerClose: false,
    relist: () => {
      isOpen = true
    },
    listed: async () => {},
  }
  on('session.messages', async () => ({ value: await w.messages() }))
  on('ui.open', () => {
    isOpen = true
    opens += 1
    return { value: { isPlaced: true } }
  })
  on('ui.panes', async () => {
    const value = isOpen ? [{ id: PANE, title: 'Clean', isShown: true, isFocused: true, isPlaced: true }] : []
    await w.listed()
    return { value }
  })
  on('ui.close', (_, e) => {
    w.closed.push(e.id)
    if (w.refuseClose) return { deny: 'kept open' }
    if (w.answerClose) return { value: undefined }
    isOpen = false
    return { value: undefined }
  })
  on('ui.scroll', () => ({}))
  on('prompt.submit', (_, e) => w.submit(e))
  on('turn.complete', () => ({ text: '' }))
  return w
}

/** A promise the test settles by hand, and one that says the hook beneath was reached. */
function gate<T>(): { reached: Promise<void>; enter: () => void; wait: Promise<T>; release: (value: T) => void } {
  let enter = () => {}
  let release: (value: T) => void = () => {}
  const reached = new Promise<void>(resolve => (enter = resolve))
  const wait = new Promise<T>(resolve => (release = resolve))
  return { reached, enter, wait, release }
}

const typed: PromptSubmitInput = { text: 'next question', origin: { kind: 'composer' }, wait: false }
const workerMessage: PromptSubmitInput = { text: 'worker report', origin: { kind: 'peer' }, wait: false }
const notice: PromptSubmitInput = { text: 'task done', origin: { kind: 'task-notification' }, wait: false }
const slashCommand: PromptSubmitInput = {
  text: '/clean copy',
  command: { name: 'clean', args: 'copy' },
  origin: { kind: 'composer' },
  wait: false,
}

const openClean = (isFullscreen: boolean, args = '') => ({
  command: 'clean',
  args,
  origin: { kind: 'composer' },
  presentation: { isFullscreen, columns: 120 },
} as const)

const turnDone = { answer: '', durationMs: 0, isAborted: false, turnId: 't1', reason: 'answer' } as const

const band = {
  plugin: 'clean-output',
  surface: 'terminal',
  component: 'AbovePrompt',
  requestId: 'band',
  props: { hasSurvey: false, isCollapsed: false, isWorking: false, maxRows: 20, bodyColumns: 80, scroll: { offset: 0, bodyRows: 19 }, view: {} },
} as const

const paneDrawing = {
  plugin: 'clean-output',
  surface: 'terminal',
  component: 'Pane',
  requestId: PANE,
  props: { title: 'Clean', isFocused: true, bodyColumns: 80, placement: 'inline', scroll: { offset: 0, bodyRows: 40 }, view: {} },
} as const

test('a prompt the person sends closes the pane; injected prompts and slash commands ask the keyboard back', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  expect(w.opens()).toBe(1)

  for (const prompt of [workerMessage, notice, slashCommand]) await $.prompt.submit(prompt)
  expect(w.closed).toEqual([])
  expect(w.opens()).toBe(4)

  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE])
  expect(w.opens()).toBe(4)
})

test('with no pane open, a notice opens nothing and a prompt closes nothing', async ($, on) => {
  const w = world(on)
  await $.prompt.submit(notice)
  await $.prompt.submit(typed)
  expect(w.opens()).toBe(0)
  expect(w.closed).toEqual([])
})

test('a notice after a typed prompt closed the pane does not reopen it', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  await $.prompt.submit(typed)
  await $.prompt.submit(notice)
  expect(w.closed).toEqual([PANE])
  expect(w.opens()).toBe(1)
})

test('a pane the person closed while a notice entered stays closed', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  const slow = gate<PromptSubmitResult>()
  w.submit = async () => {
    slow.enter()
    return slow.wait
  }
  const pending = $.prompt.submit(notice)
  await slow.reached
  w.personCloses()
  slow.release({ text: notice.text })
  await pending
  expect(w.opens()).toBe(1)
})

test('a prompt a hook beneath dropped leaves the view open', async ($, on) => {
  const w = world(on)
  w.submit = async () => ({ drop: 'blocked' })
  await $.command.run(openClean(false))
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([])

  // Still open: the next prompt that enters closes it.
  w.submit = async e => ({ text: e.text })
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE])
})

test('a prompt a hook beneath rewrote still closes the view', async ($, on) => {
  const w = world(on)
  w.submit = async () => ({ text: 'rewritten question' })
  await $.command.run(openClean(false))
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE])
})

test('a pane that closed forgets its view: its rows line, its options and its keyboard requests', async ($, on) => {
  const w = world(on)
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  await $.command.run(openClean(false))
  const origin = { kind: 'person' } as const
  await $.ui.scroll({ component: 'Pane', requestId: PANE, offset: 40, by: 40, bodyRows: 40, contentRows: 130, origin })
  let ui = await $.ui.mount(band)
  expect(await ui.find({ text: /\/clean: rows/ })).toBeDefined()
  await ui.unmount()

  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE])

  // The kit's `$` cannot read the mod's state, so the engine lists the pane
  // again to show what the mod kept: no rows line, no options (a prompt finds
  // nothing to close), no keyboard request from a notice.
  w.relist()
  ui = await $.ui.mount(band)
  expect(await ui.find({ text: /\/clean: rows/ })).toBeUndefined()
  await ui.unmount()
  await $.prompt.submit(typed)
  await $.prompt.submit(notice)
  expect(w.closed).toEqual([PANE])
  expect(w.opens()).toBe(1)
})

test('a close a hook answered for without closing keeps the view: notices refocus it, the next prompt closes it', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  w.answerClose = true
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE])

  await $.prompt.submit(notice)
  expect(w.opens()).toBe(2)

  w.answerClose = false
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE, PANE])
})

test('a prompt that entered after /clean reopened the view leaves the new view open', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  const slow = gate<PromptSubmitResult>()
  w.submit = async () => {
    slow.enter()
    return slow.wait
  }
  const pending = $.prompt.submit(typed)
  await slow.reached
  await $.command.run(openClean(false, '1'))
  slow.release({ text: typed.text })
  await pending
  expect(w.closed).toEqual([])
})

test('a refresh that read the session before /clean 1 rebuilt the view does not overwrite it', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  const slow = gate<SessionMessage[]>()
  w.messages = async () => {
    w.messages = async () => TWO_EXCHANGES
    slow.enter()
    return slow.wait
  }
  const pending = $.turn.complete(turnDone)
  await slow.reached
  await $.command.run(openClean(false, '1'))
  slow.release(THREE_EXCHANGES)
  await pending

  const ui = await $.ui.mount(paneDrawing)
  expect(await ui.find({ type: 'Text', text: /last exchange \(2 in session\)/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /all 3 exchanges/ })).toBeUndefined()
  await ui.unmount()
})

test('a pane left open through a turn rebuilds when the turn ends', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  w.messages = async () => THREE_EXCHANGES
  await $.turn.complete(turnDone)
  const ui = await $.ui.mount(paneDrawing)
  expect(await ui.find({ type: 'Text', text: /all 3 exchanges/ })).toBeDefined()
  await ui.unmount()
})

test('in fullscreen a prompt the person sends closes the band view; a worker message neither closes nor opens anything', async ($, on) => {
  const w = world(on)
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  await $.command.run(openClean(true))

  await $.prompt.submit(workerMessage)
  let ui = await $.ui.mount(band)
  expect(await ui.find({ type: 'Button', text: /close/ })).toBeDefined()
  await ui.unmount()
  expect(w.opens()).toBe(0)

  await $.prompt.submit(typed)
  ui = await $.ui.mount(band)
  expect(await ui.find({ type: 'Button', text: /close/ })).toBeUndefined()
  await ui.unmount()
  expect(w.closed).toEqual([])
})

test('a /clean that lands right after a prompt cleared the band view keeps its new view', async ($, on) => {
  world(on)
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  const cleared = gate<void>()
  let isArmed = false
  // Pauses right after the prompt's write cleared the options, before the
  // rest of its close runs.
  on('state.set', async ($, e, next) => {
    const done = await next(e)
    if (isArmed && e.key === 'options' && e.value === null) {
      isArmed = false
      cleared.enter()
      await cleared.wait
    }
    return done
  })
  await $.command.run(openClean(true))
  isArmed = true
  const pending = $.prompt.submit(typed)
  await cleared.reached
  await $.command.run(openClean(true, '1'))
  cleared.release()
  await pending

  const ui = await $.ui.mount(band)
  expect(await ui.find({ type: 'Text', text: /last exchange \(2 in session\)/ })).toBeDefined()
  await ui.unmount()
})

test('a pane closed while a notice was listing the panes is not opened again', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  const listing = gate<void>()
  w.listed = async () => {
    w.listed = async () => {}
    listing.enter()
    await listing.wait
  }
  const pending = $.prompt.submit(notice)
  await listing.reached
  // The person's prompt closes the pane while the notice's list is in flight.
  await $.prompt.submit(typed)
  listing.release()
  await pending
  expect(w.closed).toEqual([PANE])
  expect(w.opens()).toBe(1)
})

test('a close a hook refused leaves the view open: notices still refocus it, the next prompt closes it', async ($, on) => {
  const w = world(on)
  await $.command.run(openClean(false))
  w.refuseClose = true
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE])

  await $.prompt.submit(notice)
  expect(w.opens()).toBe(2)

  w.refuseClose = false
  await $.prompt.submit(typed)
  expect(w.closed).toEqual([PANE, PANE])
})
