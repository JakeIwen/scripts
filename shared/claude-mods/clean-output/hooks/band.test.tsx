import { test, expect } from 'claude-code/testing'
import type { On } from 'claude-code'

const band = {
  plugin: 'clean-output',
  surface: 'terminal',
  component: 'AbovePrompt',
  requestId: 'band',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 9 }, view: {} },
} as const

/** The world beneath the plugin: an empty band, the clean pane shown, every scroll allowed. */
function beneath(on: On): void {
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  on('ui.panes', () => ({ value: [{ id: 'clean', title: 'Clean', isShown: true, isFocused: true, isPlaced: true }] }))
  on('ui.scroll', () => ({}))
}

test('the band above the prompt stays empty until the pane reports rows past its window', async ($, on) => {
  beneath(on)
  const ui = await $.ui.mount(band)
  expect(await ui.find({ text: /\/clean: rows/ })).toBeUndefined()
})

test('once the pane scrolls, the band says which rows show and which way more lies', async ($, on) => {
  beneath(on)
  const origin = { kind: 'person' } as const
  await $.ui.scroll({ component: 'Pane', requestId: 'clean', offset: 40, by: 40, bodyRows: 40, contentRows: 130, origin })
  const ui = await $.ui.mount(band)
  expect((await ui.find({ text: /\/clean: rows/ }))?.text).toContain('rows 41–80 of 130 · more above and below')
})
