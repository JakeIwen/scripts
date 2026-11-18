// Page-side fixes from the Resources review: staging by row identity, history gaps that follow the store's thinning,
// a retry that keeps unsaved settings, the hosted restore bookmark, unknown memory pressure and unconfirmed names.
import assert from 'node:assert/strict';
import test from 'node:test';
import {Element, section} from './dom_fixture.mjs';
import {findAll, hasClass, payload, withIdentity} from './resources_fixture.mjs';

const calls = [];
let reply = {applied: 1};
const actionFetch = async (path, init) => {
  if (init.method === 'GET') return {ok: true, status: 200, json: async () => ({series: {}})};
  calls.push({path, body: JSON.parse(init.body)});
  return {ok: true, status: 200, json: async () => reply};
};
globalThis.fetch = actionFetch;

const {register, refresh} = await import('../static/panel_sections.mjs');
const resources = await import('../static/section_resources.mjs');
const {runAction} = await import('../static/resources_actions.mjs');
const {gaugeReadings} = await import('../static/resources_system.mjs');
const {chartMaxGap} = await import('../static/resources_history.mjs');
const {lineChartLayout} = await import('../static/line_chart.mjs');
const {historyPanel} = await import('../static/history_panel.mjs');
const usage = await import('../static/section_usage.mjs');
const {revealWhenRendered} = await import('../static/shell.mjs');
const {ViewMode, SortKey, view} = await import('../static/resources_processes.mjs');
const {SessionSort, sessionView} = await import('../static/resources_groups.mjs');

const noticeRoot = section('resources');
register([{id: 'resources', endpoint: null, render: () => ({node: noticeRoot, summary: '', level: 'ok'})}]);
const text = element => element.textContent;
const tick = () => new Promise(resolve => setImmediate(resolve));
const reset = () => {
  globalThis.fetch = actionFetch;
  Object.assign(view, {mode: ViewMode.APP, sort: SortKey.CPU, filter: '', paused: false});
  sessionView.sort = SessionSort.CPU;
  calls.length = 0;
  reply = {applied: 1};
};

// ---- finding 8: search reveals what it points at, and staging is keyed by (pid, started) ----

const querySelectorOf = tree => {
  const original = document.querySelector;
  document.querySelector = selector => {
    const key = /\[data-key="(.*)"\]/.exec(selector)?.[1];
    return key === undefined ? original(selector) : findAll(tree, element => element.dataset?.key === key)[0] ?? null;
  };
  return () => { document.querySelector = original; };
};

test('search never indexes a process, and every result anchors on something the page renders', () => {
  reset();
  const data = {...payload(), processes: Array.from({length: 120}, (_, n) => ({pid: 2000 + n, ppid: 1, user: 'me', name: `proc${n}`, cpu: 1, rss: 1,
    nice: 0, stopped: false, started: 'Tue Nov 17 06:00:00 2026', group: null, blocked: null}))};
  const items = resources.searchItems(data);
  assert.ok(!items.some(item => /pid \d+ ·/.test(item.subtitle)), 'the list filter finds processes');
  const {node} = resources.render(data);
  const anchors = new Set(findAll(node, element => element.dataset?.anchor).map(element => element.dataset.anchor));
  for (const item of items) assert.ok(anchors.has(item.anchor), `${item.title} points at ${item.anchor}`);
  for (const group of data.groups.filter(entry => ['claude', 'codex'].includes(entry.kind))) {
    assert.ok(items.some(item => item.title === group.name && item.anchor === `session-${group.id}`), group.name);
  }
});

test('a staged session key that no longer matches after a poll arms nothing and says so', context => {
  reset();
  context.mock.timers.enable({apis: ['setTimeout', 'Date']});
  const before = resources.render(payload());
  const staged = resources.searchItems(payload()).find(item => item.title === 'inner').actions.find(action => action.label === 'Terminate session');
  // The same pid, started again: the replacement row carries a different start token.
  const after = payload();
  after.groups.find(group => group.id === 'claude:4200').started = 'Tue Nov 17 08:00:00 2026';
  const restore = querySelectorOf(resources.render(after).node);
  staged.run();
  context.mock.timers.tick(4000);
  restore();
  const [replacement] = withIdentity(resources.render(after).node, 'session', 4200);
  assert.ok(!replacement.classes.has('armed'), 'the replacement row was not armed');
  assert.match(noticeRoot.parts['.notice'].textContent, /That session has changed since you searched/);
  assert.equal(calls.length, 0);
  const restoreBefore = querySelectorOf(before.node);
  staged.run();
  context.mock.timers.tick(1000);
  restoreBefore();
  assert.ok(withIdentity(before.node, 'session', 4200)[0].classes.has('armed'), 'the row it was staged for arms');
});

// ---- finding 9: the history gap follows the store's thinning ----

const WEEK = 7 * 86400;
const THINNED = 1440;
const line = (spacing, drop = () => false) => {
  const points = [];
  for (let ts = 0; ts <= WEEK; ts += spacing) if (!drop(ts)) points.push({ts, value: 1});
  return points;
};
const segmentsOf = (points, maxGap) => lineChartLayout({from: 0, to: WEEK, width: 800, height: 160, maxGap, series: [{key: 'claude.cpu', label: 'c', points}]}).series[0].segments.length;
const SPEC = {keys: ['claude.cpu', 'codex.cpu']};

test('a full seven-day response, 24 minutes apart, draws one continuous line', () => {
  const points = line(THINNED);
  assert.ok(points.length >= 416);
  assert.equal(segmentsOf(points, chartMaxGap(SPEC, {'claude.cpu': THINNED, 'codex.cpu': THINNED})), 1);
});

test('a real hour-long hole still breaks the line, thinned or not', () => {
  const hole = ts => ts > 3 * 86400 && ts < 3 * 86400 + 3600 + THINNED;
  assert.equal(segmentsOf(line(THINNED, hole), chartMaxGap(SPEC, {'claude.cpu': THINNED})), 2);
  const minute = ts => ts >= 3 * 86400 && ts < 3 * 86400 + 3600;
  assert.equal(segmentsOf(line(60, minute).filter(point => point.ts < 86400 * 4), chartMaxGap(SPEC, {'claude.cpu': 60})), 2);
  assert.equal(chartMaxGap(SPEC, undefined), 600, 'no spacing reported: ten missed one-minute samples');
});

test('the history panel hands the store spacing to the chart drawing', async () => {
  const seen = [];
  globalThis.fetch = async () => ({ok: true, status: 200, json: async () => ({series: {'claude.cpu': [{ts: 1, value: 1}]}, spacing: {'claude.cpu': THINNED}})});
  const panel = historyPanel({id: 'spacing-test', section: 'spacing-test', draw: (series, window, spacing) => { seen.push(spacing); return []; }, emptyText: 'none'});
  await tick();
  await tick();
  assert.ok(panel);
  assert.deepEqual(seen.at(-1), {'claude.cpu': THINNED});
});

// ---- finding 10: retrying the failed allowance read keeps the settings form ----

const tool = {declaration: {tool: 'usage-budget', title: 'Usage budget', description: 'budget', settings: [{name: 'weekly', type: 'integer', default: 5,
  description: 'weekly share', minimum: 0, maximum: 100}]}, state: {values: {weekly: 5}, revision: 'r1', path: '/budget.json'}};

for (const outcome of ['succeeds', 'fails again']) {
  test(`retrying the allowance read that ${outcome} leaves the settings form and its unsaved value in place`, async () => {
    const content = new Element();
    const root = section('usage');
    root.parts['.content'] = content;
    section('llm-restore');
    let loads = 0;
    const answer = () => (loads++ === 0 || outcome === 'fails again'
      ? {error: 'usage tool down', budget_settings: {tools: [tool]}}
      : {availability: {claude: 'ok', gpt: 'ok'}, age_minutes: 1, lean: 'balanced', budget_settings: {tools: [tool]}});
    globalThis.fetch = async path => ({ok: true, status: 200, json: async () => (String(path).startsWith('/api/history') ? {series: {}, spacing: {}} : answer())});
    register([{id: 'usage', endpoint: '/api/usage', render: usage.render}]);
    await refresh('usage');
    const field = () => findAll(content, element => element.dataset?.key === 'usage-budget-weekly')[0];
    const typed = field();
    typed.value = '42';
    findAll(content, element => element.dataset?.key === 'retry')[0].click();
    await tick();
    await tick();
    assert.equal(loads, 2);
    assert.equal(field(), typed, 'the same input element, not a rebuilt form');
    assert.equal(field().value, '42');
    assert.equal(content.textContent.includes('Could not load usage'), outcome === 'fails again');
  });
}

// ---- finding 11: the hosted restore bookmark lands whichever render finishes last ----

function bookmarkSetup(context) {
  context.mock.timers.enable({apis: ['setTimeout']});
  const host = section('usage');
  const hosted = section('llm-restore');
  const original = document.querySelector;
  const made = [];
  const anchor = () => {
    const target = new Element();
    target.dataset.anchor = 'snapshots';
    target.scrollIntoView = () => {};
    made.push(target);
    return target;
  };
  const place = target => { host.parts['[data-anchor="snapshots"]'] = target; };
  document.querySelector = selector => (selector.startsWith('main >') ? {id: 'usage'} : original(selector));
  register([
    {id: 'usage', endpoint: null, render: () => ({node: new Element(), summary: '', level: 'ok'})},
    {id: 'llm-restore', endpoint: null, render: () => { place(anchor()); return {node: new Element(), summary: '', level: 'ok'}; }},
  ]);
  return {host, hosted, place, made, done: () => { document.querySelector = original; }};
}

test('a restore bookmark lands when the host renders first and the hosted restore finishes last', async context => {
  const {made, done} = bookmarkSetup(context);
  revealWhenRendered('usage', 'snapshots', {});
  await refresh('usage');
  assert.equal(made.length, 0, 'no anchor yet: the restore frame is still loading');
  await refresh('llm-restore');
  done();
  assert.equal(made.length, 1);
  assert.ok(made[0].classList.contains('flash'), 'the anchor the hosted render supplied was revealed');
});

test('the highlight survives a stale restore frame being replaced by a later restore render', async context => {
  const {made, place, done} = bookmarkSetup(context);
  const stale = new Element();
  stale.dataset.anchor = 'snapshots';
  stale.scrollIntoView = () => {};
  place(stale);
  revealWhenRendered('usage', 'snapshots', {});
  assert.ok(stale.classList.contains('flash'));
  stale.isConnected = false;
  await refresh('llm-restore');
  await refresh('usage');
  done();
  assert.ok(made[0].classList.contains('flash'), 'the replacement anchor was highlighted');
});

// ---- finding 12: unknown pressure is not drawn as healthy ----

test('an unavailable pressure draws the memory gauge unknown and says so, with independent warnings still shown', () => {
  reset();
  const GIB = 1024 ** 3;
  const data = payload();
  data.health = {level: 'warn', reasons: ['Disk is almost full'], unavailable: ['pressure']};
  data.system.memory.pressure = null;
  const readings = gaugeReadings(data.system);
  assert.equal(readings.memory.level, 'unknown');
  assert.equal(readings.memory.valueText, '18 GB of 32 GB · pressure unavailable');
  assert.equal(gaugeReadings({memory: {total: GIB, used: GIB, pressure: 'normal'}}).memory.level, 'ok');
  const {node} = resources.render(data);
  const memory = findAll(node, element => element.dataset?.level && text(element).startsWith('Memory'))[0];
  assert.equal(memory.dataset.level, 'unknown');
  assert.match(text(node), /Disk is almost full/);
  assert.match(text(node), /Not available right now: pressure/);
});

// ---- the name source and the session confirmation ----

test('an unproven session name is marked unconfirmed with an info note; proven and absent sources show no marker', () => {
  reset();
  const render = source => {
    const data = payload();
    data.groups.find(group => group.id === 'codex:6000').name_source = source;
    return resources.render(data).node;
  };
  const marker = node => findAll(node, element => hasClass(element, 'session-name') && text(element).includes('codex · scripts'))[0];
  for (const source of ['rollout-time', 'fallback']) {
    const cell = marker(render(source));
    assert.match(text(cell), /unconfirmed name/, source);
    assert.ok(findAll(cell, element => element.dataset?.tip?.includes('could not prove')).length === 1, 'the explanation is an info popover');
  }
  for (const source of ['registry', 'rollout-open', undefined]) assert.doesNotMatch(text(marker(render(source))), /unconfirmed/, String(source));
});

test('a session confirmation names kind, pid, folder and the snapshot count, and flags an unconfirmed name', () => {
  reset();
  const data = payload();
  data.groups.find(group => group.id === 'codex:6000').name_source = 'fallback';
  const [terminate] = withIdentity(resources.render(data).node, 'session', 6000);
  terminate.click();
  assert.equal(text(terminate), 'Confirm: send terminate to Codex session "codex · scripts" (pid 6000, ~/dev/scripts, 3 processes seen, name unconfirmed)');
});

// ---- the result line describes delivery ----

test('a lower-priority reply that is all unchanged is a success; a refused one is not', async () => {
  reset();
  const target = {pid: 901, started: 'Tue Nov 17 04:00:00 2026'};
  reply = {applied: 0, gone: 0, changed: 0, skipped: 0, refused: 0, unchanged: 1};
  await runAction('lower-priority', target);
  await tick();
  assert.equal(noticeRoot.parts['.notice'].className, 'notice ok');
  assert.match(noticeRoot.parts['.notice'].textContent, /^1 process already at low priority/);
  reply = {applied: 0, gone: 0, changed: 0, skipped: 0, refused: 1, unchanged: 1};
  await runAction('lower-priority', target);
  await tick();
  assert.equal(noticeRoot.parts['.notice'].className, 'notice error');
  reply = {applied: 0, gone: 0, changed: 0, skipped: 0, refused: 0, unchanged: 1};
  await runAction('terminate', target);
  await tick();
  assert.equal(noticeRoot.parts['.notice'].className, 'notice error', 'only lower priority treats unchanged as done');
  reply = {applied: 2, gone: 0, changed: 0, skipped: 0, refused: 0, unchanged: 0};
  await runAction('kill', target);
  await tick();
  assert.match(noticeRoot.parts['.notice'].textContent, /^Sent force kill to 2 processes/);
});

test('a reply that stopped early says so after the counts, as an error', async () => {
  reset();
  const target = {pid: 6000, started: 'Tue Nov 17 07:30:00 2026'};
  reply = {applied: 2, gone: 0, changed: 0, skipped: 0, refused: 0, unchanged: 0, not_reached: 3,
    stopped: {code: 'nested-session', text: 'A session started inside this one; nothing more was signalled'}};
  await runAction('terminate', target, 'tree');
  await tick();
  const shown = noticeRoot.parts['.notice'];
  assert.equal(shown.className, 'notice error');
  assert.match(shown.textContent, /^Sent terminate to 2 processes, 3 processes not reached before the action stopped\. Stopped early: A session started inside this one; nothing more was signalled( ·|$)/);
  reply = {applied: 1, gone: 0, changed: 0, skipped: 0, refused: 0, unchanged: 0, not_reached: 0, stopped: null};
  await runAction('terminate', target);
  await tick();
  assert.equal(noticeRoot.parts['.notice'].className, 'notice ok');
  assert.doesNotMatch(noticeRoot.parts['.notice'].textContent, /Stopped/);
});

test('targets a time limit left alone read as not reached, never as protected', async () => {
  reset();
  reply = {applied: 1, gone: 0, changed: 0, skipped: 0, refused: 0, unchanged: 0, not_reached: 3,
    stopped: {code: 'time-limit', text: 'Stopped at the time limit before every process was handled; refresh and retry'}};
  await runAction('terminate', {pid: 6000, started: 'Tue Nov 17 07:30:00 2026'}, 'tree');
  await tick();
  const shown = noticeRoot.parts['.notice'].textContent;
  assert.match(shown, /3 processes not reached before the action stopped/);
  assert.doesNotMatch(shown, /protected|\[object Object\]/);
});
