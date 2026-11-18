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

// The popover is position: fixed, so its offsets are viewport coordinates rather than offsets from the icon.
test('opening, resizing and scrolling keep the info popover in the viewport', () => {
  const root = info('Information');
  const [button, pop] = root.childNodes;
  let bounds = {left: 350, top: 650, bottom: 666};
  root.getBoundingClientRect = () => bounds;
  pop.offsetWidth = 280;
  pop.offsetHeight = 400;
  button.click();
  assert.equal(pop.hidden, false);
  assert.equal(pop['--popover-left'], '98px');
  assert.equal(pop['--popover-top'], '288px');
  globalThis.innerWidth = 300;
  pop.offsetWidth = 276;
  window.dispatchEvent(new Event('resize'));
  assert.equal(pop['--popover-left'], '12px');
  bounds = {left: 40, top: 100, bottom: 116};
  window.dispatchEvent(new Event('scroll'));
  assert.equal(pop['--popover-left'], '12px');
  assert.equal(pop['--popover-top'], '122px');
  button.click();
  assert.equal(pop.hidden, true);
});

test('hovering shows the popover, leaving hides it, and a click while hovering pins it', () => {
  globalThis.innerWidth = 1440;
  const root = info('Hover text');
  const [button, pop] = root.childNodes;
  root.getBoundingClientRect = () => ({left: 1400, top: 300, bottom: 316});
  pop.offsetWidth = 280;
  pop.offsetHeight = 60;
  root.dispatchEvent(new Event('mouseenter'));
  assert.equal(pop.hidden, false);
  assert.equal(pop['--popover-left'], '1148px', 'kept inside the viewport at the right edge');
  root.dispatchEvent(new Event('mouseleave'));
  assert.equal(pop.hidden, true);
  root.dispatchEvent(new Event('mouseenter'));
  button.click();
  root.dispatchEvent(new Event('mouseleave'));
  assert.equal(pop.hidden, false, 'pinned by the click');
  assert.equal(button.attributes['aria-expanded'], 'true');
  button.click();
  assert.equal(pop.hidden, true);
});

test('keyboard focus shows the note as hover does, and aria-expanded follows every way it is shown', () => {
  globalThis.innerWidth = 1440;
  const root = info('Focus text');
  const [button, pop] = root.childNodes;
  root.getBoundingClientRect = () => ({left: 100, top: 100, bottom: 116});
  const expanded = () => button.attributes['aria-expanded'];
  button.dispatchEvent(new Event('focus'));
  assert.equal(pop.hidden, false, 'focus alone reveals it');
  assert.equal(expanded(), 'true');
  button.dispatchEvent(new Event('blur'));
  assert.equal(pop.hidden, true);
  assert.equal(expanded(), 'false');
  root.dispatchEvent(new Event('mouseenter'));
  assert.equal(expanded(), 'true', 'hover is expanded too');
  root.dispatchEvent(new Event('mouseleave'));
  assert.equal(expanded(), 'false');
  button.dispatchEvent(new Event('focus'));
  button.click();
  button.dispatchEvent(new Event('blur'));
  assert.equal(pop.hidden, false, 'a pinned note outlives focus');
  assert.equal(expanded(), 'true');
  button.click();
  assert.equal(pop.hidden, true, 'a second click closes it even while focused');
  assert.equal(expanded(), 'false');
});
