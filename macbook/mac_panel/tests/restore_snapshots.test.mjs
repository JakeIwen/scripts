import assert from 'node:assert/strict';
import test from 'node:test';
import {Element, section} from './dom_fixture.mjs';

// info() registers document-level dismissal listeners the shared fixture does not provide.
document.addEventListener ??= () => {};
globalThis.Node ??= class {};

const {register} = await import('../static/panel_sections.mjs');
const {search} = await import('../static/search.mjs');
const {searchItems} = await import('../static/section_restore.mjs');
const {resetSnapshotView, snapshotCard} = await import('../static/restore_snapshots.mjs');

const data = {
  enabled: true, installed: true, up_to_date: true, last_snapshot: 1780000000, sessions_open: 3,
  saved: [{name: 'focus-2026-11-06', path: '/s/f.sh', saved_at: 1779000000, sessions: 76}],
};

test('search finds restore and take-snapshot rows, including saved set names, and offers staging only', () => {
  const items = searchItems(data).map((item, index) => ({id: String(index), kind: 'item', section: 'llm-restore', ...item}));
  assert.equal(search(items, 'restore snapshot')[0].title, 'Restore a session snapshot');
  assert.equal(search(items, 'take snapshot')[0].title, 'Take a session snapshot');
  assert.equal(search(items, 'focus-2026-11-06')[0].title, 'Restore snapshot focus-2026-11-06');
  assert.deepEqual(search(items, 'take snapshot')[0].actions.map(action => action.label), ['Name snapshot']);
});

test('the snapshot card lists the automatic row and each saved set with its session count', () => {
  const text = snapshotCard(data).textContent;
  assert.match(text, /Latest automatic snapshot/);
  assert.match(text, /focus-2026-11-06/);
  assert.match(text, /76 sessions/);
  assert.match(text, /Take snapshot/);
});

// ---- interaction regressions with deferred fetch responses ----

const twoSets = {...data, saved: [
  {name: 'a', path: '/s/a.sh', saved_at: 10, sessions: 1}, {name: 'b', path: '/s/b.sh', saved_at: 20, sessions: 2}]};

// Actions refresh their section afterwards; this stand-in keeps that path real without a page.
section('llm-restore');
let rendered = null;
register([{id: 'llm-restore', endpoint: null, render: () => {
  rendered = snapshotCard(twoSets);
  return {node: new Element(), summary: '', level: 'idle'};
}}]);

const walk = (element, found = []) => {
  if (typeof element !== 'object') return found;
  found.push(element);
  element.childNodes.forEach(child => walk(child, found));
  return found;
};
const picks = card => walk(card).filter(element => element.dataset?.target);
const pick = (card, target) => picks(card).find(element => element.dataset.target === target);
const output = card => walk(card).filter(element => element.className === 'snapshot-output-slot')[0].textContent;
const settle = () => new Promise(resolve => setImmediate(resolve));

function deferredFetch() {
  resetSnapshotView();
  const requests = [];
  globalThis.fetch = (path, init) => new Promise(resolve => {
    const body = JSON.parse(init.body);
    requests.push({body, reply: (report, ok = true) => resolve({
      ok, status: ok ? 200 : 500, json: async () => ok ? {message: 'ok', report} : {error: report},
    })});
  });
  return requests;
}

test('only the newest preview response applies, even when an older one lands last', async () => {
  const requests = deferredFetch();
  const card = snapshotCard(twoSets);
  pick(card, 'saved:a').click();
  pick(card, 'saved:b').click();
  pick(card, 'saved:a').click();
  pick(card, 'saved:a').click();
  assert.equal(requests.length, 4);
  requests[3].reply('latest a');
  requests[0].reply('stale a');
  requests[1].reply('stale b');
  requests[2].reply('stale a again');
  await settle();
  assert.match(output(card), /latest a/);
  assert.doesNotMatch(output(card), /stale|Loading/);
});

test('a refresh whose selected source changed asks for a new dry run', async () => {
  const requests = deferredFetch();
  const first = snapshotCard(twoSets);
  pick(first, 'saved:a').click();
  requests[0].reply('old a');
  await settle();
  const same = snapshotCard(twoSets);
  assert.equal(requests.length, 1, 'unchanged data reuses the preview');
  assert.match(output(same), /old a/);
  const replaced = snapshotCard({...twoSets, saved: [{...twoSets.saved[0], saved_at: 99}, twoSets.saved[1]]});
  assert.equal(requests.length, 2);
  assert.doesNotMatch(output(replaced), /old a/);
  requests[1].reply('new a');
  await settle();
  assert.match(output(replaced), /new a/);
  pick(replaced, 'auto').click();
  requests[2].reply('auto preview');
  await settle();
  snapshotCard({...twoSets, last_snapshot: 1780000099});
  assert.equal(requests.length, 4, 'the automatic list changed when last_snapshot moved');
});

test('a restore report stays labelled with its target when another row is selected', async () => {
  const requests = deferredFetch();
  const card = snapshotCard(twoSets);
  pick(card, 'saved:a').click();
  requests[0].reply('preview a');
  await settle();
  const restore = walk(card).find(element => element.dataset?.key === 'restore-selected');
  restore.click();
  restore.click(); // second click confirms
  await settle();
  assert.deepEqual(requests[1].body, {action: 'restore', target: {kind: 'saved', name: 'a'}, confirmed: true});
  pick(card, 'saved:b').click();
  requests[1].reply('restored a');
  await settle();
  await settle();
  assert.match(output(rendered), /Restore report: a[\s\S]*restored a/);
  assert.doesNotMatch(output(rendered), /Restore report: b/);
});

test('a save report shows with nothing selected', async () => {
  const requests = deferredFetch();
  const card = snapshotCard(twoSets);
  const take = walk(card).find(element => element.dataset?.key === 'take-snapshot');
  walk(card).find(element => element.dataset?.key === 'snapshot-name').value = '';
  take.click();
  assert.deepEqual(requests[0].body, {action: 'save'});
  requests[0].reply('Saved 3 open session(s) to /s/2026-11-14-2200.sh');
  await settle();
  assert.match(output(rendered), /Snapshot report[\s\S]*2026-11-14-2200/);
});

const control = (card, key) => walk(card).find(element => element.dataset?.key === key);
const confirmClick = element => { element.click(); element.click(); };

test('Delete and Overwrite exist only for a saved set, and lock the row while one runs', async () => {
  const requests = deferredFetch();
  const card = snapshotCard(twoSets);
  pick(card, 'auto').click();
  requests[0].reply('auto preview');
  await settle();
  assert.equal(control(card, 'delete-selected'), undefined);
  assert.equal(control(card, 'overwrite-selected'), undefined);
  pick(card, 'saved:a').click();
  requests[1].reply('preview a');
  await settle();
  const [restore, overwrite, remove] = ['restore', 'overwrite', 'delete'].map(kind => control(card, `${kind}-selected`));
  assert.equal(remove.textContent, 'Delete');
  remove.click();
  assert.equal(remove.textContent, 'Confirm: delete a', 'the first click only arms');
  assert.equal(requests.length, 2);
  remove.click();
  assert.deepEqual(requests[2].body, {action: 'delete', target: {kind: 'saved', name: 'a'}, confirmed: true});
  assert.equal(restore.disabled && overwrite.disabled && remove.disabled, true, 'the three never overlap');
  requests[2].reply('Moved /s/a.sh to /s/.deleted/a-1.sh');
  await settle();
  await settle();
  assert.match(output(rendered), /Delete report: a[\s\S]*\.deleted/);
  assert.equal(control(rendered, 'restore-selected'), undefined, 'a deleted set is no longer selected');
  assert.equal(requests.length, 3, 'no dry run is fetched for nothing');
});

test('Overwrite arms with its own wording and fetches a new dry run afterwards', async () => {
  const requests = deferredFetch();
  const card = snapshotCard(twoSets);
  pick(card, 'saved:b').click();
  requests[0].reply('preview b');
  await settle();
  const overwrite = control(card, 'overwrite-selected');
  overwrite.click();
  assert.equal(overwrite.textContent, 'Confirm: overwrite b');
  overwrite.click();
  assert.equal(requests[1].body.action, 'overwrite');
  requests[1].reply('Saved 5 open session(s)');
  await settle();
  await settle();
  assert.match(output(rendered), /Overwrite report: b/);
  assert.equal(requests.length, 3, 'the preview of the replaced set is requested again');
  assert.equal(requests[2].body.action, 'preview');
});

test('a bare set name ranks Restore first; the verb brings Delete or Overwrite for that set first', () => {
  const items = searchItems(twoSets).map((item, index) => ({id: String(index), kind: 'item', section: 'llm-restore', ...item}));
  assert.equal(search(items, 'b')[0].title, 'Restore snapshot b');
  const remove = search(items, 'delete b')[0];
  assert.equal(remove.title, 'Delete a saved snapshot');
  assert.match(remove.subtitle, /^b ·/);
  assert.match(search(items, 'overwrite a')[0].subtitle, /^a ·/);
});

test('running every search action only stages: it arms at most, and never posts', async context => {
  const requests = deferredFetch();
  context.mock.timers.enable({apis: ['setTimeout']});
  const {button} = await import('../static/panel_dom.mjs');
  const {searchTarget, setSearchTarget} = await import('./dom_fixture.mjs');
  let clicks = 0;
  const actions = searchItems(twoSets).flatMap(item => item.actions ?? []);
  assert.ok(actions.length >= 6);
  for (let round = 0; round < 2; round++) {
    for (const found of actions) {
      setSearchTarget(button('Go', () => { clicks++; }, {confirm: 'Confirm: go'}));
      found.run();
      context.mock.timers.tick(1000);
    }
  }
  assert.equal(clicks, 0);
  assert.equal(requests.filter(request => request.body.action !== 'preview').length, 0, 'no delete, overwrite or save was posted');
});

test('reselecting a row mid-delete leaves all three of its buttons disabled', async () => {
  const requests = deferredFetch();
  const card = snapshotCard(twoSets);
  pick(card, 'saved:a').click();
  requests[0].reply('preview a');
  await settle();
  confirmClick(control(card, 'delete-selected'));
  assert.equal(requests[1].body.action, 'delete');
  pick(card, 'saved:a').click();
  const fresh = ['restore', 'overwrite', 'delete'].map(kind => control(card, `${kind}-selected`));
  assert.deepEqual(fresh.map(element => element.disabled), [true, true, true]);
  pick(card, 'saved:b').click();
  assert.equal(control(card, 'delete-selected').disabled, false, 'another row stays usable');
  requests[1].reply('Moved');
  await settle();
  await settle();
  assert.equal(control(rendered, 'restore-selected'), undefined);
});
