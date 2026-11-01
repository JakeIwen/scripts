import { test, expect } from 'claude-code/testing'

import { highlightTerms } from './highlight'

const TERMS = '**Terms**\n- **race condition**: two writers interleave.\n- **hash**: a digest.\n- **hash map**: a keyed table.\n'

test('bolds the first mention of each term in the prose, not the Terms block', () => {
  const reply = `A race condition here, another race condition there.\n\n${TERMS}`
  expect(highlightTerms(reply)).toBe(`A **race condition** here, another race condition there.\n\n${TERMS}`)
})

test('matches plurals and any case, but not inside a longer word', () => {
  const reply = `Rehashing aside, Hashes and Race Conditions.\n\n${TERMS}`
  expect(highlightTerms(reply)).toBe(`Rehashing aside, **Hashes** and **Race Conditions**.\n\n${TERMS}`)
})

test('a longer term is bolded first and shields the shorter one inside it', () => {
  const reply = `Use a hash map; the hash is cheap.\n\n${TERMS}`
  expect(highlightTerms(reply)).toBe(`Use a **hash map**; the **hash** is cheap.\n\n${TERMS}`)
})

test('skips mentions in code, links and fences, and leaves a term already bold', () => {
  const reply = [
    '`race condition` and [race condition](https://example.com) are skipped.',
    '```',
    'hash map',
    '```',
    'The **hash map** is bold already; a race condition here.',
    '',
    TERMS,
  ].join('\n')
  expect(highlightTerms(reply)).toBe(reply.replace('a race condition here', 'a **race condition** here'))
})

test('a reply without a Terms block, or one still streaming its first entry, is unchanged', () => {
  expect(highlightTerms('A race condition.')).toBe('A race condition.')
  expect(highlightTerms('A race condition.\n\n**Terms**\n- **race cond')).toBe('A race condition.\n\n**Terms**\n- **race cond')
})

test('the render hook hands the engine the highlighted markdown', async ($, on) => {
  // Stands for the engine's own drawing of the props the mod passes on.
  let drawnText = ''
  on('ui.render', { component: 'AssistantMessage' }, ($, e) => {
    drawnText = e.props.text
    return $.ui.resolve(e).Markdown({ text: e.props.text })
  })
  await $.ui.mount({
    plugin: 'term-highlight',
    surface: 'terminal',
    component: 'AssistantMessage',
    props: { text: `One race condition.\n\n${TERMS}`, isFirstOfReply: true },
  })
  expect(drawnText).toContain('One **race condition**.')
})
