import assert from 'node:assert/strict';
import test from 'node:test';
import {Element} from './dom_fixture.mjs';

document.addEventListener ??= () => {};
globalThis.Node ??= Element;
document.createElementNS ??= (_namespace, tag) => new Element(tag);
const {readyDetails, stateLabel, waitingNotReady} = await import('../static/section_deferred_ready.mjs');
const {render} = await import('../static/section_deferred.mjs');

const READY = `ssh -o BatchMode=yes vanpi.lan 'sudo python3 -c "import json;print(\\"x\\")"'`;
const base = {
  id: '2026-11-14-check', valid: true, name: 'check', due: '2026-11-14T09:00', state: 'pending', attempts: 0,
  max_attempts: 3, timeout: 900, description: 'd', created: '2026-11-13T09:00', origin: '/tmp', last_log: null,
  give_up: '2026-11-21T09:00', agent: true, ready: READY, last_not_ready: '2026-11-15T08:00', not_ready_checks: 18,
};
const after = new Date(2026, 10, 15, 9, 0).valueOf();
const text = element => element.textContent;

test('an agent job with a ready command shows it verbatim in a wrapping monospace block', () => {
  const block = readyDetails(base);
  assert.match(text(block), /^Ready condition/);
  const pre = block.childNodes[1].childNodes[0];
  assert.equal(pre.tagName, 'PRE');
  assert.equal(pre.textContent, READY);
  assert.match(text(block), /Last check.*not ready at 2026-11-15 08:00 \(18 checks\)/);
  assert.match(text(block), /Gives up.*2026-11-21 09:00 \(/);
});

test('an agent job without --ready says it runs as soon as it is due', () => {
  const block = readyDetails({...base, ready: null, last_not_ready: null, not_ready_checks: 0});
  assert.match(text(block), /None: runs as soon as it's due/);
  assert.doesNotMatch(text(block), /Last check/);
  assert.match(text(block), /Gives up/);
});

test('a plain job points at its own script and the exit status 75 convention', () => {
  const block = readyDetails({...base, agent: false, ready: null, not_ready_checks: 1});
  assert.match(text(block), /Decided by the job's own script \(exit 75 = not ready\)/);
  assert.match(text(block), /\(1 check\)/);
});

test('past due with a not-ready check reads waiting, otherwise the plain state', () => {
  assert.equal(waitingNotReady(base, after), true);
  assert.equal(stateLabel(base, after), 'waiting: not ready');
  assert.equal(stateLabel({...base, last_not_ready: null}, after), 'pending');
  assert.equal(stateLabel(base, new Date(2026, 10, 13).valueOf()), 'pending');
  assert.equal(stateLabel({...base, state: 'failed'}, after), 'failed');
});

test('the rendered card carries the disclosure and the waiting status', () => {
  const data = {checker: {agent_loaded: true, stale: false, last_run: null}, jobs_dir: '/j', tasks: [], jobs: [base]};
  const {node} = render(data);
  const all = text(node);
  assert.match(all, /waiting: not ready/);
  assert.match(all, /Ready condition/);
  assert.ok(all.includes(READY));
});
