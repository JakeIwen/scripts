import assert from 'node:assert/strict';
import test from 'node:test';
import {searchTarget, setSearchTarget} from './dom_fixture.mjs';

const {arm, button} = await import('../static/dom_core.mjs');
const {stageAction} = await import('../static/search_actions.mjs');

test('arming twice never dispatches a click or runs a handler, and Escape/blur/timeout still disarm', context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  let calls = 0;
  const control = button('Run', () => { calls++; }, {confirm: 'Confirm: run'});
  arm(control);
  arm(control);
  assert.equal(control.textContent, 'Confirm: run');
  assert.equal(calls, 0);
  const escape = new Event('keydown');
  escape.key = 'Escape';
  control.dispatchEvent(escape);
  assert.equal(control.textContent, 'Run');
  arm(control);
  control.dispatchEvent(new Event('blur'));
  assert.equal(control.textContent, 'Run');
  arm(control);
  context.mock.timers.tick(8000);
  assert.equal(control.textContent, 'Run');
  control.click();
  assert.equal(calls, 0, 'the first user click still only arms');
  control.click();
  assert.equal(calls, 1, 'only a confirming user click runs');
});

test('search retry arms an unarmed replacement after an armed button disappears', context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  let calls = 0;
  setSearchTarget(button('Run', () => { calls++; }, {confirm: 'Confirm: run'}));
  stageAction('crontab', 'fixture', 'run', {arm: true});
  context.mock.timers.tick(100);
  assert.equal(searchTarget.textContent, 'Confirm: run');
  searchTarget.isConnected = false;
  setSearchTarget(button('Run', () => { calls++; }, {confirm: 'Confirm: run'}));
  context.mock.timers.tick(400);
  assert.equal(searchTarget.textContent, 'Confirm: run');
  assert.equal(calls, 0);
  searchTarget.click();
  assert.equal(calls, 1);
});

test('search retry does not re-arm after confirmation', context => {
  context.mock.timers.enable({apis: ['setTimeout']});
  let calls = 0;
  const original = button('Run', () => { calls++; }, {confirm: 'Confirm: run'});
  setSearchTarget(original);
  stageAction('crontab', 'fixture', 'run', {arm: true});
  context.mock.timers.tick(100);
  original.click();
  original.isConnected = false;
  setSearchTarget(button('Run', () => { calls++; }, {confirm: 'Confirm: run'}));
  context.mock.timers.tick(400);
  assert.equal(searchTarget.textContent, 'Run');
  assert.equal(calls, 1);
});
