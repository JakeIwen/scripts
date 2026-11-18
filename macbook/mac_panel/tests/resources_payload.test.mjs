// Renders the page from a real backend payload (names replaced), so the page and /api/resources cannot drift apart unnoticed.
// A Python contract test keeps the same file in shape-sync with resources_adapter.read().
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {section} from './dom_fixture.mjs';
import {findAll, hasClass, labelsIn, withKey} from './resources_fixture.mjs';

const real = () => JSON.parse(readFileSync(new URL('./resources_payload.json', import.meta.url), 'utf8'));

globalThis.fetch = async () => ({ok: true, status: 200, json: async () => ({series: {}})});
const {register} = await import('../static/panel_sections.mjs');
const resources = await import('../static/section_resources.mjs');
const {ViewMode, view} = await import('../static/resources_processes.mjs');
register([{id: 'resources', endpoint: null, render: () => ({node: section('resources'), summary: '', level: 'ok'})}]);

const text = element => element.textContent;

test('every card renders from the real payload with the real group ids and vocabulary', () => {
  const data = real();
  const {node, level} = resources.render(data);
  const content = text(node);
  assert.equal(level, 'warn');
  for (const heading of ['System', 'LLM sessions', 'LLM infrastructure', 'm4-compute', 'Apps and processes', 'History']) assert.ok(content.includes(heading), heading);
  assert.match(content, /Memory pressure\s*Elevated/);
  assert.match(content, /Thermal\s*Nominal/);
  assert.match(content, /Power\s*Battery · 40 %/);
  assert.ok(content.includes('Memory pressure is elevated'));
  const sessions = data.groups.filter(group => ['claude', 'codex'].includes(group.kind));
  const rows = findAll(node, element => hasClass(element, 'session'));
  assert.equal(rows.length, sessions.length);
  for (const group of sessions) assert.ok(content.includes(group.name), group.name);
  for (const group of data.groups.filter(entry => entry.kind === 'infra')) assert.ok(content.includes(group.name), group.name);
});

test('a nested session sits under the session that contains it', () => {
  const data = real();
  const inner = data.groups.find(group => group.parent);
  const keys = findAll(resources.render(data).node, element => hasClass(element, 'session')).map(element => element.dataset.key);
  assert.equal(keys[keys.indexOf(`session-${inner.id}`) - 1], `session-${inner.parent}`);
});

test('the compute worker is the worker row, not a job, so the idle line shows', () => {
  const content = text(resources.render(real()).node);
  assert.ok(content.includes('idle: no job is running'));
});

test('tree_blocked sessions offer no terminate button and show their reason; the others offer one', () => {
  const data = real();
  const {node} = resources.render(data);
  for (const group of data.groups.filter(entry => ['claude', 'codex'].includes(entry.kind))) {
    assert.equal(withKey(node, `session-${group.root_pid}`).length, group.tree_blocked ? 0 : 1, group.name);
  }
  assert.ok(findAll(node, element => element.dataset?.tip).some(element => element.dataset.tip.includes('end those first')));
});

test('process rows tag the kind taken from the group id and offer buttons only when not blocked', () => {
  Object.assign(view, {mode: ViewMode.PROCESS, filter: ''});
  const data = real();
  const {node} = resources.render(data);
  const member = data.processes.find(row => row.group && !row.blocked);
  assert.equal(withKey(node, `terminate-${member.pid}`).length, 1);
  const blocked = data.processes.find(row => row.blocked);
  assert.equal(withKey(node, `terminate-${blocked.pid}`).length, 0);
  assert.ok(text(node).includes('Claude'), 'kind tag from claude:<pid>');
  Object.assign(view, {mode: ViewMode.APP});
});

test('summary, search and attention items read the real payload without error', () => {
  const data = real();
  const {summary} = resources.render(data);
  assert.match(summary, /CPU 11 %/);
  const titles = resources.searchItems(data).map(item => item.title);
  assert.ok(titles.includes('System'));
  assert.ok(data.groups.filter(group => ['claude', 'codex'].includes(group.kind)).every(group => titles.includes(group.name)));
  assert.ok(resources.attentionItems(data).some(item => item.text === 'Memory pressure is elevated'));
  assert.ok(labelsIn(resources.render(data).node).length >= 0);
});
