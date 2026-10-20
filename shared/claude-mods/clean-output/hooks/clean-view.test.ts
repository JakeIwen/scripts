import { test, expect } from 'claude-code/testing'

import { lastTurnKey, newestTarget, sectionKey, windowLine } from './clean-view'

const section = (lastTurnAt?: number) => ({ prompt: 'p', reply: 'one\n\n_after x_\n\ntwo', lastTurnAt })

test('the pane opens on the newest last turn, else the newest section, else stays at the top', () => {
  expect(newestTarget({ label: '', sections: [section(), section(5)] })).toBe(lastTurnKey(1))
  expect(newestTarget({ label: '', sections: [section(5), section()] })).toBe(sectionKey(1))
  expect(newestTarget({ label: '', sections: [section()] })).toBeUndefined()
  expect(newestTarget({ label: '', sections: [] })).toBeUndefined()
})

test('the status line names the rows in view and which way more lies; none when all fit', () => {
  expect(windowLine({ offset: 0, bodyRows: 40, contentRows: 130 })).toBe('/clean: rows 1–40 of 130 · more below (↓ PgDn End)')
  expect(windowLine({ offset: 40, bodyRows: 40, contentRows: 130 })).toContain('rows 41–80 of 130 · more above and below')
  expect(windowLine({ offset: 90, bodyRows: 40, contentRows: 130 })).toContain('rows 91–130 of 130 · more above')
  expect(windowLine({ offset: 0, bodyRows: 40, contentRows: 30 })).toBeUndefined()
  expect(windowLine({ offset: 0, bodyRows: 0, contentRows: 0 })).toBeUndefined()
})
