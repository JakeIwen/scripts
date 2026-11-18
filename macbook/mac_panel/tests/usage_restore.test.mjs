import assert from 'node:assert/strict';
import test from 'node:test';
import {Element, section} from './dom_fixture.mjs';

// info() registers document-level dismissal listeners the shared fixture does not provide.
document.addEventListener ??= () => {};
globalThis.Node ??= class {};

const {action, refresh, register, state} = await import('../static/panel_sections.mjs');
const {attentionItems, cardExtra, render, searchItems} = await import('../static/section_usage.mjs');
const {hostedIn, resolveSection} = await import('../static/shell.mjs');

const usage = {availability: {claude: 'ok', gpt: 'ok'}, age_minutes: 1, lean: 'balanced', budget_settings: null};
const walk = (element, found = []) => {
  if (typeof element !== 'object') return found;
  found.push(element);
  element.childNodes.forEach(child => walk(child, found));
  return found;
};

test('the LLM section mounts the llm-restore frame under the budgets and shows it', () => {
  const frame = section('llm-restore');
  frame.hidden = true;
  const result = render(usage);
  assert.ok(walk(result.node).includes(frame));
  assert.equal(frame.hidden, false);
  assert.equal(result.level, 'ok');
});

test('a usage failure shows an error in the allowance block; the restore frame and settings card stay', () => {
  const frame = section('llm-restore');
  const data = {error: 'usage tool down', budget_settings: null};
  const result = render(data);
  assert.match(result.node.textContent, /Could not load usage: usage tool down/);
  assert.match(result.node.textContent, /could not be loaded/i);
  assert.ok(walk(result.node).includes(frame));
  assert.equal(result.level, 'error');
  assert.deepEqual(attentionItems(data), []);
  assert.equal(cardExtra(data), null);
  assert.ok(searchItems(data).length > 0);
});

function mockFetch(failing) {
  globalThis.fetch = async () => failing
    ? {ok: false, status: 500, json: async () => ({error: 'restore adapter down'})}
    : {ok: true, status: 200, json: async () => ({message: 'ok'})};
}

function setupSections() {
  const field = new Element('input');
  field.value = 'unsaved budget';
  field.defaultValue = '';
  section('usage', field);
  section('llm-restore');
  let usageRenders = 0;
  register([
    {id: 'usage', endpoint: null, render: () => { usageRenders++; return {node: new Element(), summary: '', level: 'ok'}; }},
    {id: 'llm-restore', endpoint: null, render: () => ({node: new Element(), summary: '', level: 'idle'})},
  ]);
  return {field, renders: () => usageRenders};
}

for (const failing of [false, true]) {
  test(`a restore action that ${failing ? 'fails' : 'succeeds'} leaves the LLM section and its unsaved field alone`, async () => {
    const {field, renders} = setupSections();
    mockFetch(failing);
    await action('llm-restore', '/api/llm-restore', {enabled: false});
    await refresh('llm-restore');
    assert.equal(renders(), 0);
    assert.equal(field.value, 'unsaved budget');
  });
}

test('a failed restore load records an error for its own section only', async () => {
  const {renders} = setupSections();
  globalThis.fetch = async () => ({ok: false, status: 500, json: async () => ({error: 'restore adapter down'})});
  register([{id: 'llm-restore', render: () => ({node: new Element(), summary: '', level: 'idle'})}]);
  await refresh('llm-restore');
  assert.equal(state['llm-restore'].error, 'restore adapter down');
  assert.equal(renders(), 0);
});

test('the retired llm-restore id opens the LLM section at the snapshots block', () => {
  const ids = ['overview', 'usage', 'llm-restore'];
  assert.deepEqual(resolveSection('llm-restore', ids), {id: 'usage', anchorId: 'snapshots', frame: 'llm-restore'});
  assert.deepEqual(resolveSection('usage', ids), {id: 'usage', anchorId: undefined, frame: 'usage'});
  assert.equal(resolveSection('nope', ids), null);
  assert.equal(resolveSection(null, ids), null);
  assert.deepEqual(hostedIn('usage'), ['llm-restore']);
});
