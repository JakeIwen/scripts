import assert from 'node:assert/strict';
import test from 'node:test';
import {Element, section} from './dom_fixture.mjs';

const {register} = await import('../static/panel_sections.mjs');
const {modeBlock} = await import('../static/usage_mode.mjs');
const flush = () => new Promise(resolve => setImmediate(resolve));

const find = (root, match) => {
  if (match(root)) return root;
  for (const child of root.childNodes ?? []) {
    const found = typeof child === 'object' ? find(child, match) : null;
    if (found) return found;
  }
  return null;
};

function setup(context) {
  const field = new Element('input');
  field.value = 'unsaved budget';
  field.defaultValue = '';
  const root = section('usage', field);
  let renders = 0;
  register([{id: 'usage', render: () => { renders++; return {node: new Element()}; }}]);
  const block = modeBlock({lean: 'balanced-burn', mode_source: 'auto'}, 'usage');
  const apply = find(block, element => element.tagName === 'BUTTON');
  return {field, root, block, apply, renders: () => renders};
}

const press = apply => { apply.click(); apply.click(); };

test('applying a mode keeps a dirty settings field: the mode block is redrawn and the reload waits', async context => {
  const {field, root, block, apply, renders} = setup(context);
  context.mock.method(globalThis, 'fetch', async () => ({ok: true, json: async () => ({lean: 'gpt-burn', mode_source: 'manual', message: 'set'})}));
  press(apply);
  await flush();
  assert.equal(block.replacedWith.tagName, 'DIV');
  assert.match(block.replacedWith.textContent, /gpt-burn/);
  assert.equal(renders(), 0);
  assert.equal(field.value, 'unsaved budget');
  assert.match(root.parts['.meta'].textContent, /paused/);
});

test('a failed mode apply shows the error and refreshes nothing', async context => {
  const {block, apply, root, renders} = setup(context);
  context.mock.method(globalThis, 'fetch', async () => ({ok: false, status: 400, json: async () => ({error: 'refused'})}));
  press(apply);
  await flush();
  assert.match(root.parts['.notice'].textContent, /refused|failed|400/i);
  assert.equal(renders(), 0);
  assert.equal(block.replacedWith, undefined);
});
