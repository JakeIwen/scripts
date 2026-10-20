import { test, expect } from 'claude-code/testing'

import { markdownToHtml, markdownToPlain } from './markdown'

test('plain text drops inline marks but keeps code spans as written', () => {
  expect(markdownToPlain('**Mark reviewed** is a _read_ marker; run `a_b*c` now.')).toBe(
    'Mark reviewed is a read marker; run a_b*c now.',
  )
  expect(markdownToPlain('see [the guide](https://x.dev/g) or <https://y.dev>')).toBe('see the guide (https://x.dev/g) or https://y.dev')
  expect(markdownToPlain('snake_case_name stays')).toBe('snake_case_name stays')
})

test('plain text keeps list markers, unfences code, and aligns tables', () => {
  const md = '## Status\n- **one**\n  - two\n\n```sh\nls **/x\n```\n\n| a | bb |\n|---|---|\n| ccc | d |'
  expect(markdownToPlain(md)).toBe('Status\n- one\n  - two\n\nls **/x\n\na    bb\nccc  d')
})

test('html escapes text and builds lists, tables, code and paragraphs', () => {
  const html = markdownToHtml('Use <b> **now**\n\n- one\n- `x<y`\n\n| h |\n|---|\n| v |\n\n```\na < b\nc\n```')
  expect(html).toContain('<p>\nUse &lt;b&gt; <strong>now</strong>\n</p>')
  expect(html).toContain('<ul>\n<li>one</li>\n<li><code>x&lt;y</code></li>\n</ul>')
  expect(html).toContain('<tr><th>h</th></tr>\n<tr><td>v</td></tr>')
  expect(html).toContain('<pre><code>a &lt; b\nc</code></pre>')
})
