import { test, expect } from 'claude-code/testing'

import { frontmatter, labelFor, mismatch, resolveChoice, shortModel, withLabel } from './label'

const PARENT = { model: 'claude-opus-5-5', effort: 'xhigh' }

test('model ids shorten to family and version; gateway ids lose their context suffix', () => {
  expect(shortModel('claude-sonnet-5-5')).toBe('sonnet 5.5')
  expect(shortModel('claude-haiku-4-5-20251001')).toBe('haiku 4.5')
  expect(shortModel('claude-gpt-6.1-sol[1m]')).toBe('gpt-6.1-sol')
  expect(shortModel('sonnet')).toBe('sonnet')
})

test('the call outranks the agent file, which outranks the parent', () => {
  const file = { model: 'claude-gpt-6-luna[1m]', effort: 'low' }
  expect(resolveChoice({ model: 'haiku', effort: 'high' }, file, PARENT)).toEqual({ model: 'haiku', effort: 'high' })
  expect(resolveChoice({}, file, PARENT)).toEqual({ model: 'claude-gpt-6-luna[1m]', effort: 'low' })
  expect(resolveChoice({ subagent_type: 'Explore' }, {}, PARENT)).toEqual(PARENT)
  expect(resolveChoice({ model: 'sonnet' }, { model: 'inherit' }, PARENT)).toEqual({ model: 'sonnet', effort: 'xhigh' })
})

test('a fork runs on the parent whatever the call says', () => {
  expect(resolveChoice({ subagent_type: 'fork', model: 'haiku', effort: 'low' }, {}, PARENT)).toEqual(PARENT)
})

test('frontmatter fields are read with quotes stripped, the body ignored', () => {
  const text = '---\nname: sol\nmodel: "claude-gpt-6.1-sol[1m]"\neffort: high\n---\n\nmodel: not this\n'
  expect(frontmatter(text)).toEqual({ name: 'sol', model: 'claude-gpt-6.1-sol[1m]', effort: 'high' })
  expect(frontmatter('no frontmatter')).toEqual({})
})

test('a trailing YAML comment is not part of the value, but a # inside one is', () => {
  const text = '---\nmodel: sonnet # preferred\neffort: "high" # for reviews\nname: a#b\n---\n'
  expect(frontmatter(text)).toEqual({ model: 'sonnet', effort: 'high', name: 'a#b' })
})

test('the label shortens the model and replaces an old tag', () => {
  const label = labelFor({ model: 'claude-sonnet-5-5', effort: 'high' })
  expect(label).toBe('[sonnet 5.5 · high]')
  expect(labelFor({ model: 'claude-gpt-6-luna[1m]' })).toBe('[gpt-6-luna]')
  expect(withLabel('[opus 5.5 · xhigh] Read the logs', label)).toBe('[sonnet 5.5 · high] Read the logs')
})

test('an alias matches any id of its family; a fallback or another effort is a mismatch', () => {
  expect(mismatch({ model: 'sonnet', effort: 'high' }, { model: 'claude-sonnet-5-5', effort: 'high' })).toBeUndefined()
  expect(mismatch({ model: 'claude-gpt-6-luna[1m]', effort: 'xhigh' }, { model: 'claude-gpt-6-luna[1m]', effort: 'xhigh' })).toBeUndefined()
  expect(mismatch({ model: 'claude-gpt-6.1-sol[1m]', effort: 'high' }, { model: 'claude-opus-5-5', effort: 'high' }))
    .toBe('runs on [opus 5.5 · high], not [gpt-6.1-sol · high]')
  expect(mismatch({ model: 'haiku', effort: 'low' }, { model: 'claude-haiku-5-5' })).toBe('runs on [haiku 5.5], not [haiku · low]')
})
