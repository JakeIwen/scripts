import assert from 'node:assert/strict';
import test from 'node:test';
import {Element, section} from './dom_fixture.mjs';

const {refreshIfIdle, refreshPending, register} = await import('../static/panel_sections.mjs');
const {watchTask} = await import('../static/task_receipt.mjs');
const flush = () => new Promise(resolve => setImmediate(resolve));

test('task completion preserves dirty fields, then the shared poll guard refreshes after editing ends', async context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  const field = new Element('input');
  field.value = 'unsaved script';
  field.defaultValue = '';
  const root = section('deferred', field);
  root.parts['.content'].append(field);
  let renders = 0;
  register([{id: 'deferred', render: () => ({node: new Element(), summary: 'updated', level: 'ok'})}].map(entry => ({
    ...entry, render(value) { renders++; return entry.render(value); },
  })));
  const calls = [];
  context.mock.method(globalThis, 'fetch', async path => {
    calls.push(path);
    return {ok: true, json: async () => path.startsWith('/api/tasks/')
      ? {state: 'done', finished_at: 123, exit_status: 0, message: 'Completed'} : {jobs: []}};
  });
  watchTask('deferred', {id: 'dirty-task'}, 'Run fixture');
  context.mock.timers.tick(2000);
  await flush();
  assert.match(root.parts['.notice'].textContent, /done, exit 0/);
  assert.equal(renders, 0);
  assert.deepEqual(root.parts['.content'].childNodes, [field]);
  assert.equal(field.value, 'unsaved script');
  assert.equal(refreshPending('deferred'), true);
  assert.deepEqual(calls, ['/api/tasks/dirty-task']);
  field.defaultValue = field.value;
  await refreshIfIdle('deferred');
  assert.equal(renders, 1);
  assert.equal(refreshPending('deferred'), false);
});

test('edits begun during a task-completion GET also pause the render', async context => {
  const field = new Element('textarea');
  field.value = field.defaultValue = '';
  const root = section('crontab', field);
  let renders = 0;
  register([{id: 'crontab', render: () => { renders++; return {node: new Element()}; }}]);
  let respond;
  context.mock.method(globalThis, 'fetch', () => new Promise(resolve => { respond = resolve; }));
  const pending = refreshIfIdle('crontab');
  field.value = 'new edit';
  respond({ok: true, json: async () => ({jobs: []})});
  await pending;
  assert.equal(renders, 0);
  assert.equal(refreshPending('crontab'), true);
  assert.equal(field.value, 'new edit');
  assert.match(root.parts['.meta'].textContent, /paused/);
});
