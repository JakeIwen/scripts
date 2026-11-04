import { test, expect } from 'claude-code/testing'

import { levelFor } from './levels'

test('letters map to /effort levels; anything else is refused', () => {
  expect(levelFor('x')).toBe('xhigh')
  expect(levelFor(' M ')).toBe('max')
  expect(levelFor('U')).toBeUndefined()
  expect(levelFor('toString')).toBeUndefined()
  expect(levelFor('')).toBeUndefined()
})
