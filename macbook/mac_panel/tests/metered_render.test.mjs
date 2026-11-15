import assert from 'node:assert/strict';
import test from 'node:test';
import './dom_fixture.mjs';

document.addEventListener ??= () => {};
globalThis.Node ??= class {};
const {render} = await import('../static/section_metered.mjs');

function payload(actual, fresh = true) {
  return {
    fresh, state: {verdict: {metered: false, reason: 'unmetered'}, effective: {source: 'verdict'},
      consumers: actual === null ? {} : {'mac.low_data_mode': {actual, reason: 'Controller reason'}}},
    low_data_mode: {enabled: true, revision: 'r', error: null},
    policy: {mode: {value: 'auto'}, exempt: [], approvals: {}},
    config: {metered_ssids: [], revision: 1}, pending: [], sync: {}, registry: [], pi_state: null,
  };
}

test('renders actual Wi-Fi state and the automatic control', () => {
  const cases = [
    ['held', 'Wi-Fi now: constrained (Low Data Mode on)'],
    ['running', 'Wi-Fi now: not constrained'],
    ['stopped_manually', 'Wi-Fi now: constrained (native or unowned Low Data Mode)'],
    ['n/a', 'Wi-Fi now: unknown (automatic control off)'],
    ['unknown', 'Wi-Fi now: unknown'],
    [null, 'Wi-Fi now: unknown'],
  ];
  for (const [actual, expected] of cases) {
    const text = render(payload(actual)).node.textContent;
    assert.ok(text.includes('Automatic Low Data Mode'));
    assert.ok(text.includes(expected), `${actual}: ${text}`);
    if (actual !== null && actual !== 'n/a') assert.ok(text.includes('Controller reason'));
    assert.ok(text.includes('Only this Mac’s Wi-Fi interface (en0) is affected.'));
  }
  const stale = render(payload('held', false)).node.textContent;
  assert.ok(stale.includes('Wi-Fi now: unknown'));
  assert.ok(!stale.includes('Controller reason'));
});
