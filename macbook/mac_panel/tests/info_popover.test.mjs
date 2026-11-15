import assert from 'node:assert/strict';
import test from 'node:test';
import './dom_fixture.mjs';

document.addEventListener = () => {};
document.documentElement = {};
globalThis.window = new EventTarget();
globalThis.innerWidth = 390;
globalThis.innerHeight = 700;
globalThis.getComputedStyle = () => ({getPropertyValue: () => '12px'});
const {info} = await import('../static/components.mjs');

test('opening, resizing and scrolling keep the info popover in the viewport', () => {
  const root = info('Information');
  const [button, pop] = root.childNodes;
  let bounds = {left: 350, top: 650, bottom: 666};
  root.getBoundingClientRect = () => bounds;
  pop.offsetWidth = 280;
  pop.offsetHeight = 400;
  button.click();
  assert.equal(pop.hidden, false);
  assert.equal(pop['--popover-left'], '-252px');
  assert.equal(pop['--popover-top'], '-362px');
  globalThis.innerWidth = 300;
  pop.offsetWidth = 276;
  window.dispatchEvent(new Event('resize'));
  assert.equal(pop['--popover-left'], '-338px');
  bounds = {left: 40, top: 100, bottom: 116};
  window.dispatchEvent(new Event('scroll'));
  assert.equal(pop['--popover-left'], '-28px');
  assert.equal(pop['--popover-top'], '22px');
  button.click();
  assert.equal(pop.hidden, true);
});
