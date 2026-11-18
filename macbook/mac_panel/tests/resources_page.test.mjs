import assert from 'node:assert/strict';
import test from 'node:test';
import {section} from './dom_fixture.mjs';
import {buttonsIn, findAll, hasClass, labelsIn, payload, withIdentity, withKey} from './resources_fixture.mjs';

const calls = [];
let reply = {applied: 1};
globalThis.fetch = async (path, init) => {
  if (init.method === 'GET') return {ok: true, status: 200, json: async () => ({series: {}})};
  calls.push({path, body: JSON.parse(init.body)});
  return {ok: true, status: 200, json: async () => reply};
};

const {register} = await import('../static/panel_sections.mjs');
const resources = await import('../static/section_resources.mjs');
const {GAUGE_THRESHOLDS, HEALTH_LEVEL, gaugeReadings, healthLevel} = await import('../static/resources_system.mjs');
const {ActionId, Scope, processActions, requestBody, resultText} = await import('../static/resources_actions.mjs');
const {SessionSort, orderSessions, processTree, sessionView} = await import('../static/resources_groups.mjs');
const {ViewMode, SortKey, WINDOW_ROWS, listWindow, view, visibleRows} = await import('../static/resources_processes.mjs');
const {Level} = await import('../static/status_cell.mjs');

const noticeRoot = section('resources');
register([{id: 'resources', endpoint: null, render: () => ({node: noticeRoot, summary: '', level: 'ok'})}]);
const text = element => element.textContent;
const reset = () => { Object.assign(view, {mode: ViewMode.APP, sort: SortKey.CPU, filter: '', paused: false}); sessionView.sort = SessionSort.CPU; calls.length = 0; reply = {applied: 1}; };
const tick = () => new Promise(resolve => setImmediate(resolve));

test('renders every card from the full fixture: sessions by name, nesting, totals, read-only rows', () => {
  reset();
  const {node, summary, level} = resources.render(payload());
  const content = text(node);
  assert.equal(level, Level.OK);
  assert.match(summary, /CPU 38 %/);
  assert.match(summary, /18 GB used/);
  for (const heading of ['System', 'LLM sessions', 'LLM infrastructure', 'm4-compute', 'Apps and processes', 'History']) {
    assert.ok(content.includes(heading), heading);
  }
  const sessions = findAll(node, element => hasClass(element, 'session'));
  assert.deepEqual(sessions.map(element => element.dataset.key),
    ['session-claude:4100', 'session-claude:4200', 'session-codex:6000', 'session-claude:5000'], 'default sort is CPU, busiest first');
  assert.ok(content.includes('Claude'), 'kind tag');
  assert.ok(content.includes('Codex'));
  assert.match(content, /Claude: 3 sessions · 9 processes/);
  assert.match(content, /Codex: 1 session · 3 processes/);
  assert.ok(content.includes('model gateway'), 'infra row');
  assert.ok(content.includes('worker'), 'compute worker row');
  assert.ok(content.includes('idle: no job is running'));
});

test('nested sessions sit directly under the session they started in, by the outer group id', () => {
  const groups = payload().groups.filter(group => group.kind === 'claude');
  const order = orderSessions(groups).map(({group, depth}) => `${depth}:${group.name}`);
  assert.deepEqual(order, ['0:panel work', '1:inner', '0:agent-b'], 'the default is CPU, busiest first');
  const byName = orderSessions(groups, SessionSort.NAME).map(({group, depth}) => `${depth}:${group.name}`);
  assert.deepEqual(byName, ['0:agent-b', '0:panel work', '1:inner']);
  const nested = [...groups, {...groups[0], id: 'claude:4300', name: 'second inner', parent: 'claude:4100', cpu: 50, rss: 1}];
  const memory = orderSessions(nested, SessionSort.MEMORY).map(({group}) => group.name);
  assert.deepEqual(memory, ['panel work', 'inner', 'second inner', 'agent-b'], 'children stay under their parent, ordered by the sort');
  const cpu = orderSessions(nested, SessionSort.CPU).map(({group}) => group.name);
  assert.deepEqual(cpu, ['panel work', 'second inner', 'inner', 'agent-b']);
});

test('the session sort headers re-order the rows, mark the chosen one and survive a re-render', () => {
  reset();
  const {node} = resources.render(payload());
  const keys = list => findAll(list, element => hasClass(element, 'session')).map(element => element.dataset.key);
  const [byMemory] = withKey(node, 'session-sort-rss');
  byMemory.click();
  assert.equal(sessionView.sort, SessionSort.MEMORY);
  assert.deepEqual(keys(node), ['session-claude:4100', 'session-claude:4200', 'session-codex:6000', 'session-claude:5000']);
  assert.equal(withKey(node, 'session-sort-rss')[0].attributes['aria-pressed'], 'true');
  assert.equal(withKey(node, 'session-sort-cpu')[0].attributes['aria-pressed'], 'false');
  withKey(node, 'session-sort-name')[0].click();
  assert.deepEqual(keys(node), ['session-claude:5000', 'session-codex:6000', 'session-claude:4100', 'session-claude:4200']);
  assert.deepEqual(keys(resources.render(payload()).node), keys(node), 'a poll keeps the choice');
});

test('a session tree is a table flagged for the compact phone layout, one row per process, indented by depth', () => {
  const group = payload().groups.find(entry => entry.id === 'claude:4100');
  const tree = processTree(group);
  assert.ok(tree.classes.has('tree-table'));
  const rows = findAll(tree, element => element.tagName === 'TR');
  assert.equal(rows.length, group.processes.length + 1);
  const heads = findAll(rows[0], element => element.tagName === 'TH').map(element => element.textContent);
  assert.deepEqual(heads, ['Process', 'PID', 'CPU', 'Memory', 'Age']);
  const depths = findAll(tree, element => hasClass(element, 'tree-name')).map(element => element['--depth']);
  assert.deepEqual(depths, group.processes.map(proc => String(proc.depth)));
  const [name, pid, cpu] = findAll(rows[3], element => element.tagName === 'TD');
  assert.equal(name.textContent, 'node');
  assert.equal(pid.dataset.label, 'PID');
  assert.equal(cpu.textContent, '4.9 %');
});

test('health levels map through one table, anything else is unknown', () => {
  assert.deepEqual(Object.keys(HEALTH_LEVEL), ['ok', 'warn', 'error', 'unknown']);
  assert.equal(healthLevel({level: 'warn'}), Level.WARN);
  assert.equal(healthLevel({level: 'error'}), Level.ERROR);
  assert.equal(healthLevel({level: 'surprising'}), Level.UNKNOWN);
  assert.equal(healthLevel(null), Level.UNKNOWN);
});

test('unavailable probes read as unavailable and every null is tolerated', () => {
  const data = {...payload(), health: {level: 'unknown', reasons: [], unavailable: ['pressure', 'thermal']},
    system: {cpu_percent: null, ncpu: null, load: null, process_count: null, uptime_seconds: null,
      memory: {total: 32 * 1024 ** 3, used: null, swap_used: null, swap_total: null, pressure: null}, thermal: null, power: null, disk: null},
    totals: {}, groups: [], apps: [], processes: []};
  const {node, level} = resources.render(data);
  const content = text(node);
  assert.equal(level, Level.UNKNOWN);
  assert.match(content, /Not available right now: pressure, thermal/);
  assert.match(content, /Memory pressure\s*unavailable/);
  assert.match(content, /CPU\s*unavailable/);
  assert.match(content, /Disk\s*unavailable/);
  assert.ok(content.includes('No Claude or Codex sessions are running.'));
  assert.ok(content.includes('No LLM infrastructure processes found.'));
  assert.ok(content.includes('The van-compute worker is not running.'));
  assert.ok(content.includes('No processes were listed.'));
  assert.deepEqual(resources.searchItems(data).map(item => item.title), ['System', 'LLM sessions', 'm4-compute', 'Apps and processes', 'History']);
});

test('a render with no system object at all still draws', () => {
  const {node} = resources.render({health: null, groups: [], apps: [], processes: []});
  assert.match(text(node), /resources unavailable|System/);
});

test('a compute job row shows beside the worker and removes the idle line', () => {
  const data = payload();
  data.groups.push({id: 'compute:810', kind: 'compute', name: 'job-render', detail: 'ffmpeg', status: null, parent: null, root_pid: 810,
    started: 'Tue Nov 17 07:50:00 2026', cpu: 90, rss: 2 * 1024 ** 3, count: 3, tree_blocked: null, processes: []});
  const content = text(resources.render(data).node);
  assert.ok(content.includes('job-render'));
  assert.ok(!content.includes('idle: no job is running'));
});

test('a compute row named "job · <program>" is a job, so the idle line goes', () => {
  const data = payload();
  data.groups.push({...data.groups.find(group => group.kind === 'compute'), id: 'compute:811', name: 'job · ffmpeg', root_pid: 811});
  assert.ok(!text(resources.render(data).node).includes('idle: no job is running'));
});

test('pressure, thermal and power use the backend vocabulary', () => {
  const data = payload();
  data.system.memory.pressure = 'critical';
  data.system.thermal = 'warning';
  data.system.power = {source: 'ups', percent: 50, charging: null};
  const content = text(resources.render(data).node);
  assert.match(content, /Memory pressure\s*Critical/);
  assert.match(content, /Thermal\s*Warning recorded/);
  assert.match(content, /Power\s*UPS · 50 %/);
});

test('swap with no total reads as none in use, not as an empty gauge', () => {
  const data = payload();
  data.system.memory.swap_total = 0;
  data.system.memory.swap_used = 0;
  assert.match(text(resources.render(data).node), /Swap\s*none in use/);
});

test('the System gauges read what is used, with colour thresholds in one table and memory graded by pressure', () => {
  const GIB = 1024 ** 3;
  const readings = gaugeReadings({cpu_percent: 20, memory: {total: 24 * GIB, used: 20 * GIB, pressure: 'warn', swap_total: 5 * GIB, swap_used: 4 * GIB},
    disk: {total: 926 * GIB, free: 294 * GIB}});
  assert.deepEqual(readings.cpu, {value: 20, max: 100, worse: GAUGE_THRESHOLDS.cpu, valueText: '20 % used'});
  assert.deepEqual(readings.memory, {value: 20 * GIB, max: 24 * GIB, level: 'warn'});
  assert.deepEqual(readings.swap, {value: 4 * GIB, max: 5 * GIB, worse: GAUGE_THRESHOLDS.swap});
  assert.deepEqual(readings.disk, {value: 632 * GIB, max: 926 * GIB, worse: GAUGE_THRESHOLDS.disk});
  assert.deepEqual(Object.keys(GAUGE_THRESHOLDS), ['cpu', 'swap', 'disk']);
  const calm = gaugeReadings({memory: {total: GIB, used: GIB, pressure: 'normal', swap_total: 0, swap_used: 0}});
  assert.equal(calm.memory.level, 'ok', 'a full memory at normal pressure is not an alarm');
  assert.deepEqual(calm.swap, {note: 'none in use'});
  assert.equal(calm.cpu, null);
  assert.equal(gaugeReadings({memory: {total: GIB, used: GIB, pressure: 'critical'}}).memory.level, 'error');
});

test('blocked rows show no buttons, only their reason behind an info popover', () => {
  reset();
  view.mode = ViewMode.PROCESS;
  const {node} = resources.render(payload());
  assert.equal(withIdentity(node, 'terminate', 1200).length, 0);
  assert.equal(withIdentity(node, 'terminate', 700).length, 0);
  assert.equal(withIdentity(node, 'terminate', 901).length, 1);
  const infoTips = findAll(node, element => element.dataset?.tip).map(element => element.dataset.tip);
  assert.ok(infoTips.includes('Not owned by you.'));
  assert.ok(infoTips.includes('Shared by every session; restart it from Services.'));
  assert.ok(infoTips.includes('Owned by van-compute; killing a job here would skip its cleanup.'));
});

test('Pause follows stopped and Lower priority hides at nice 10 or more', () => {
  assert.deepEqual(processActions({stopped: false, nice: 0}), ['terminate', 'kill', 'pause', 'lower-priority']);
  assert.deepEqual(processActions({stopped: true, nice: 5}), ['terminate', 'kill', 'resume', 'lower-priority']);
  assert.deepEqual(processActions({stopped: false, nice: 10}), ['terminate', 'kill', 'pause']);
  assert.deepEqual(processActions({stopped: false, nice: 19}), ['terminate', 'kill', 'pause']);
});

test('the Terminate session button names the session and its snapshot count; a nested group is blocked with its reason', () => {
  reset();
  const {node} = resources.render(payload());
  assert.equal(withIdentity(node, 'session', 4100).length, 0, 'tree_blocked group has no button');
  assert.ok(text(node).includes('Terminate session is unavailable'));
  const [terminate] = withIdentity(node, 'session', 4200);
  terminate.click();
  assert.equal(text(terminate), 'Confirm: send terminate to Claude session "inner" (pid 4200, ~/dev/scripts/pi, 2 processes seen)');
  assert.equal(calls.length, 0, 'the first click only arms');
});

test('confirming posts the exact process body and reports the reply counts', async () => {
  reset();
  view.mode = ViewMode.PROCESS;
  const {node} = resources.render(payload());
  const [lower] = withIdentity(node, 'lower-priority', 901);
  lower.click();
  assert.equal(text(lower), 'Confirm: lower the priority of Safari (pid 901)');
  assert.equal(calls.length, 0);
  reply = {applied: 1, unchanged: 0};
  lower.click();
  await tick();
  assert.deepEqual(calls, [{path: '/api/resources', body: {action: 'lower-priority', pid: 901, started: 'Tue Nov 17 04:00:00 2026', scope: 'process', confirmed: true}}]);
});

test('a session terminate posts tree scope with the group root and its start token', async () => {
  reset();
  const {node} = resources.render(payload());
  const [terminate] = withIdentity(node, 'session', 6000);
  terminate.click();
  terminate.click();
  await tick();
  assert.deepEqual(calls[0].body, {action: 'terminate', pid: 6000, started: 'Tue Nov 17 07:30:00 2026', scope: 'tree', confirmed: true});
});

test('request bodies carry confirmed, pid, started, scope and action for every action', () => {
  for (const id of Object.values(ActionId)) {
    for (const scope of Object.values(Scope)) {
      assert.deepEqual(requestBody(id, {pid: 7, started: 'token', name: 'x'}, scope), {action: id, pid: 7, started: 'token', scope, confirmed: true});
    }
  }
});

test('result text lists the reply counts in plain words and leaves zeros out', () => {
  assert.equal(resultText('terminate', {applied: 12, gone: 1, changed: 0, skipped: 2, refused: 0, unchanged: 0}),
    'Sent terminate to 12 processes, 1 process already gone, 2 processes skipped as protected');
  assert.equal(resultText('lower-priority', {applied: 0, unchanged: 1}), '1 process already at low priority');
  assert.equal(resultText('kill', {applied: 0, changed: 2, refused: 1}), 'Nothing was signalled, 2 processes changed since the snapshot and left alone, 1 process refused by the system');
});

test('the result notice is shown after a confirmed action', async () => {
  reset();
  reply = {applied: 3, gone: 1, changed: 0, skipped: 0, refused: 0, unchanged: 0};
  view.mode = ViewMode.PROCESS;
  const {node} = resources.render(payload());
  const [pause] = withIdentity(node, 'pause', 901);
  pause.click();
  pause.click();
  await tick();
  await tick();
  assert.match(noticeRoot.parts['.notice'].textContent, /Paused 3 processes, 1 process already gone/);
});

test('sort, view and filter survive a second render', () => {
  reset();
  resources.render(payload());
  view.mode = ViewMode.PROCESS;
  view.sort = SortKey.MEMORY;
  view.filter = 'node';
  const second = resources.render(payload());
  const content = text(second.node);
  assert.ok(content.includes('showing 2 of 2 matching'));
  const [filter] = findAll(second.node, element => element.dataset?.key === 'process-filter');
  assert.equal(filter.value, 'node');
  const controls = findAll(second.node, element => hasClass(element, 'process-controls'))[0];
  const pressed = findAll(controls, element => element.attributes?.['aria-pressed'] === 'true').map(element => element.textContent);
  assert.ok(pressed.includes('By process') && pressed.includes('Memory'));
  assert.ok(!pressed.includes('By app') && !pressed.includes('CPU'));
});

test('typing in the filter redraws only the list and keeps the input element', () => {
  reset();
  const {node} = resources.render(payload());
  const [filter] = findAll(node, element => element.dataset?.key === 'process-filter');
  filter.value = 'safari';
  filter.dispatchEvent(new Event('input'));
  assert.equal(view.filter, 'safari');
  assert.equal(filter.defaultValue, 'safari', 'a poll is not held back as an unsaved edit');
  assert.ok(text(node).includes('showing 1 of 1 matching'));
  assert.equal(findAll(node, element => element.dataset?.key === 'process-filter')[0], filter);
});

test('visibleRows filters on name, pid and group and sorts by the chosen key, ties by name', () => {
  const rows = [{name: 'b', pid: 2, cpu: 5, rss: 1}, {name: 'a', pid: 3, cpu: 5, rss: 9}, {name: 'c', pid: 4, cpu: 1, rss: 99, group: 'claude:12'}];
  assert.deepEqual(visibleRows(rows, {sort: 'cpu', filter: ''}).map(row => row.name), ['a', 'b', 'c']);
  assert.deepEqual(visibleRows(rows, {sort: 'rss', filter: ''}).map(row => row.name), ['c', 'a', 'b']);
  assert.deepEqual(visibleRows(rows, {sort: 'cpu', filter: 'CLAUDE'}).map(row => row.name), ['c']);
  assert.deepEqual(visibleRows(rows, {sort: 'cpu', filter: '3'}).map(row => row.name), ['a']);
});

const manyProcesses = count => Array.from({length: count}, (_, n) => ({pid: 1000 + n, ppid: 1, user: 'me', name: n === 77 ? 'cfprefsd' : `proc${n}`,
  cpu: n === 77 ? 0 : 100 - n / 10, rss: (count - n) * 1024, nice: 0, stopped: n === 90 || n === 91, started: 'Tue Nov 17 06:00:00 2026', group: null, blocked: null}));
const countRows = root => findAll(findAll(root, element => hasClass(element, 'process-list'))[0], element => element.tagName === 'TR').length - 1;

test('the process list draws the first 50 matches and says how many more match, so a name always finds a row', () => {
  reset();
  view.mode = ViewMode.PROCESS;
  const data = {...payload(), processes: manyProcesses(212)};
  const {node} = resources.render(data);
  assert.equal(countRows(node), WINDOW_ROWS);
  assert.ok(text(node).includes('showing 50 of 212'));
  assert.ok(!text(node).includes('cfprefsd'), 'idle process 77 sorts below the window');
  const [filter] = findAll(node, element => element.dataset?.key === 'process-filter');
  filter.value = 'cfprefsd';
  filter.dispatchEvent(new Event('input'));
  assert.equal(countRows(node), 1);
  assert.ok(text(node).includes('cfprefsd'));
  assert.ok(text(node).includes('showing 1 of 1 matching'));
  filter.value = 'proc';
  filter.dispatchEvent(new Event('input'));
  assert.ok(text(node).includes('showing 50 of 211 matching'));
  assert.equal(listWindow({rows: rows => rows.processes, pausedApplies: true}, data).rows.length, WINDOW_ROWS);
});

test('the Paused chip narrows By process to stopped rows, keeps its state across a render and is hidden in By app', () => {
  reset();
  view.mode = ViewMode.PROCESS;
  const data = {...payload(), processes: manyProcesses(212)};
  const {node} = resources.render(data);
  const [chip] = withKey(node, 'process-paused');
  assert.equal(chip.textContent, 'Paused (2)');
  assert.equal(chip.attributes['aria-pressed'], 'false');
  chip.click();
  assert.equal(view.paused, true);
  assert.equal(chip.attributes['aria-pressed'], 'true');
  assert.equal(countRows(node), 2);
  assert.ok(text(node).includes('showing 2 of 2 matching'));
  assert.equal(withKey(resources.render(data).node, 'process-paused')[0].attributes['aria-pressed'], 'true', 'a poll keeps it');
  withKey(node, 'view-app')[0].click();
  assert.ok(!text(node).includes('matching'), 'the chip does not narrow By app, which has no stopped rows');
  assert.equal(countRows(node), data.apps.length);
  view.mode = ViewMode.PROCESS;
});

test('the paused tag stays on a stopped row', () => {
  reset();
  view.mode = ViewMode.PROCESS;
  assert.ok(text(resources.render(payload()).node).includes('paused'));
});

test('search indexes sections, cards and sessions, never individual processes, and only stages', () => {
  reset();
  const items = resources.searchItems(payload());
  assert.ok(!items.some(item => /^pid \d+/.test(item.subtitle)), 'the list filter finds processes, not search');
  for (const card of ['System', 'LLM sessions', 'm4-compute', 'Apps and processes', 'History']) assert.ok(items.some(item => item.title === card), card);
  const inner = items.find(item => item.title === 'inner');
  assert.deepEqual(inner.actions.map(action => action.label), ['Open', 'Terminate session']);
  const panelWork = items.find(item => item.title === 'panel work');
  assert.deepEqual(panelWork.actions.map(action => action.label), ['Open'], 'tree_blocked offers no terminate');
  assert.equal(calls.length, 0);
});

test('attention items come from the backend health only: a paused process is not an alarm', () => {
  const data = payload();
  assert.ok(data.processes.some(row => row.stopped), 'the fixture has a paused process');
  assert.deepEqual(resources.attentionItems(data), []);
  data.health = {level: 'warn', reasons: ['Memory pressure is high'], unavailable: ['thermal']};
  const items = resources.attentionItems(data);
  assert.deepEqual(items.map(item => item.level), [Level.WARN, Level.WARN]);
  assert.equal(items[0].text, 'Memory pressure is high');
  assert.equal(items[1].text, 'Resource probes unavailable: thermal');
  data.health = {level: 'error', reasons: ['Memory pressure is critical'], unavailable: []};
  assert.equal(resources.attentionItems(data)[0].level, Level.ERROR);
});

test('every kind is tagged and the By app view has no action buttons', () => {
  reset();
  const {node} = resources.render(payload());
  const labels = labelsIn(node);
  assert.ok(!labels.includes('Terminate') && !labels.includes('Force kill'), 'app view and read-only rows offer none');
  assert.ok(buttonsIn(node).length > 0);
});

test('history series drop nulls, keep real zeros and skip a chart with nothing to draw', async () => {
  const {chartSeries} = await import('../static/resources_history.mjs');
  const series = {'claude.cpu': [{ts: 1, value: 0}, {ts: 2, value: null}, {ts: 3, value: 12}], 'codex.cpu': [{ts: 1, value: null}]};
  const [cpu] = chartSeries({keys: ['claude.cpu', 'codex.cpu', 'infra.cpu'], styles: () => ({color: 'accent'})}, series);
  assert.equal(chartSeries({keys: ['claude.cpu', 'codex.cpu', 'infra.cpu'], styles: () => ({})}, series).length, 1);
  assert.deepEqual(cpu.points.map(point => point.value), [0, 12]);
  assert.equal(cpu.label, 'Claude');
});

test('choosing a view or sort updates the pressed state at once, without a re-render', () => {
  reset();
  const {node} = resources.render(payload());
  const controls = findAll(node, element => hasClass(element, 'process-controls'))[0];
  const pressed = () => findAll(controls, element => element.attributes?.['aria-pressed'] === 'true').map(element => element.textContent);
  withKey(node, 'view-process')[0].click();
  withKey(node, 'sort-rss')[0].click();
  assert.ok(pressed().includes('By process') && pressed().includes('Memory'));
  assert.ok(!pressed().includes('By app') && !pressed().includes('CPU'));
});
