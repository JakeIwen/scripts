import assert from 'node:assert/strict';
import test from 'node:test';

import {buildIndex, scoreItem, scoreTerm, search, settingItems, words} from '../static/search.mjs';

const item = (title, extra = {}) => ({id: title, kind: 'item', title, section: 'services', ...extra});

test('words splits on punctuation and lowercases', () => {
  assert.deepEqual(words('llm-restore: Snapshot_ON'), ['llm', 'restore', 'snapshot', 'on']);
  assert.deepEqual(words(null), []);
});

test('scoreTerm ranks exact word over prefix over substring over subsequence', () => {
  const scores = ['restart', 'restarts', 'prerestart', 'rstrt'].map(text => scoreTerm('restart', text));
  assert.ok(scores[0] > scores[1] && scores[1] > scores[2], `${scores}`);
  assert.ok(scoreTerm('rstr', 'restart') > 0, 'subsequence still matches');
  assert.equal(scoreTerm('rst', 'restart'), 0, 'subsequence needs four letters');
  assert.equal(scoreTerm('zzz', 'restart'), 0);
  assert.equal(scoreTerm('x', ''), 0);
});

test('title outranks subtitle outranks keywords for the same word', () => {
  const inTitle = scoreItem(['panel'], item('Panel'));
  const inSubtitle = scoreItem(['panel'], item('Other', {subtitle: 'Panel'}));
  const inKeywords = scoreItem(['panel'], item('Other', {keywords: ['panel']}));
  assert.ok(inTitle > inSubtitle && inSubtitle > inKeywords);
  assert.ok(inKeywords > 0);
});

test('every query word must match something', () => {
  assert.equal(scoreItem(['restart', 'nothing'], item('Restart mac-panel')), 0);
  assert.ok(scoreItem(['restart', 'panel'], item('Restart mac-panel')) > 0);
});

test('search orders by score, then section before row, and honours the limit', () => {
  const items = [
    item('Deal watch pause', {kind: 'action'}),
    {id: 's', kind: 'section', title: 'Services', section: 'services'},
    item('Services log'),
    item('pause'),
  ];
  assert.deepEqual(search(items, 'services').map(found => found.id), ['s', 'Services log']);
  assert.deepEqual(search(items, 'pause').map(found => found.id), ['Deal watch pause', 'pause'], 'equal scores: action before plain row');
  assert.deepEqual(search(items, '   '), []);
  assert.equal(search(items, 'a', 2).length <= 2, true);
});

const settings = {tools: [
  {tool: 'dimmer', declaration: {tool: 'dimmer', title: 'Cab dimmer', settings: [
    {name: 'dim_percent', description: 'How far to dim', label: 'Dim level'},
    {name: 'enabled', description: 'Run the dimmer'},
  ]}},
  {tool: 'broken', error: 'cannot load'},
]};

test('settings fields become rows titled by tool and label, skipping tools that failed to load', () => {
  const rows = settingItems(settings);
  assert.deepEqual(rows.map(row => [row.title, row.anchor]),
    [['Cab dimmer: Dim level', 'dimmer-dim_percent'], ['Cab dimmer: enabled', 'dimmer-enabled']]);
  assert.equal(search(rows, 'dim level')[0].anchor, 'dimmer-dim_percent');
  assert.deepEqual(settingItems(undefined), []);
});

test('buildIndex lists sections, multi-section groups, settings and each section\'s own rows', () => {
  const sections = [
    {id: 'services', title: 'Services', group: 'Jobs & services',
      searchItems: payload => payload.labels.map(label => ({title: `Restart ${label}`, kind: 'action', anchor: label}))},
    {id: 'deferred', title: 'Deferred jobs', group: 'Jobs & services', searchItems: () => { throw new Error('boom'); }},
    {id: 'settings', title: 'Settings', group: 'Settings'},
  ];
  const groups = [{id: 'jobs', title: 'Jobs & services', sections: ['services', 'deferred']},
    {id: 'settings', title: 'Settings', sections: ['settings']}];
  const payloads = {services: {labels: ['mac-panel']}, settings, deferred: {}};
  const index = buildIndex({sections, groups, payloadOf: id => payloads[id]});
  assert.deepEqual(index.filter(row => row.kind === 'group').map(row => row.title), ['Jobs & services']);
  const restart = search(index, 'restart mac-panel')[0];
  assert.equal(restart.section, 'services');
  assert.equal(restart.anchor, 'mac-panel');
  assert.equal(search(index, 'dim level')[0].section, 'settings');
  assert.equal(search(index, 'deferred')[0].kind, 'section');
  const unloaded = buildIndex({sections, groups, payloadOf: () => undefined});
  assert.equal(search(unloaded, 'restart').length, 0);
});
