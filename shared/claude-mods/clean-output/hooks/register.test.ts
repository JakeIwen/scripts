import { test, expect } from 'claude-code/testing'

import { parseArgs } from './clean'

test('words combine in any order; a number is a count, none means every exchange', () => {
  expect(parseArgs('3 copy')).toEqual({ count: 3, copy: true, markdown: false, tools: false, turns: false })
  expect(parseArgs('tools md')).toEqual({ count: 'all', copy: false, markdown: true, tools: true, turns: false })
  expect(parseArgs('1').count).toBe(1)
})

const paneProps = {
  title: 'Clean',
  isFocused: true,
  bodyColumns: 80,
  placement: 'inline',
  scroll: { offset: 0, bodyRows: 20 },
  view: {},
} as const

test('the pane draws a hint before /clean has run', async $ => {
  const ui = await $.ui.mount({
    plugin: 'clean-output',
    surface: 'terminal',
    component: 'Pane',
    requestId: 'clean',
    props: paneProps,
  })
  expect(await ui.find({ type: 'Markdown' })).toBeDefined()
  expect(await ui.find({ type: 'Button', text: /copy/ })).toBeDefined()
})
