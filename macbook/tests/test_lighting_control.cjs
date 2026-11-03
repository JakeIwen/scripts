const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');
const folder = path.resolve(__dirname, '../bettertouchtool/lighting_control');
const Model = require(path.join(folder, 'state.js'));
const Bridge = require(path.join(folder, 'bridge.js'));
const {Kind} = Model;
const fixture = (id = 'one', extra = {}) => ({entity_id: `light.${id}`, label: id, group_label: 'Room',
  available: true, state: 'on', hue: 25, saturation: null, brightness: 60, color_mode: 'hs',
  color_temp_kelvin: 3000, min_color_temp_kelvin: 2200, max_color_temp_kelvin: 6500,
  supports_hue: true, supports_color_temperature: true, ...extra});
const reply = lights => ({ok: true, lights});
const turn = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
function harness(lights = [fixture()]) {
  const calls = [];
  const transport = {read: async () => reply(lights), write: (entities, change) => {
    const gate = deferred();
    calls.push({entities, change, gate});
    return gate.promise;
  }};
  return {calls, transport, model: Model.create({lights, transport})};
}

test('shell commands quote the CLI path and contain only validated fixed arguments', () => {
  const cli = "/some path/it's.py";
  assert.equal(Bridge.buildCommand(cli), "/usr/bin/python3 -B '/some path/it'\\''s.py' lights");
  assert.equal(Bridge.buildCommand(cli, ['light.one', 'light.two'], {kind: Kind.colour, hue: 360, saturation: 0}),
    "/usr/bin/python3 -B '/some path/it'\\''s.py' set light.one light.two --hue 360 --saturation 0");
  assert.match(Bridge.buildCommand(cli, ['light.one'], {kind: Kind.white, kelvin: 7000}), / --kelvin 7000$/);
  assert.match(Bridge.buildCommand(cli, ['light.one'], {kind: Kind.brightness, brightness: 1}), / --brightness 1$/);
  for (const on of [true, false]) assert.match(Bridge.buildCommand(cli, ['light.one'], {kind: Kind.power, on}), on ? / --on$/ : / --off$/);
  assert.doesNotMatch(Bridge.buildCommand(cli, ['light.one'], {kind: Kind.white, kelvin: 2000, hue: '$(evil)'}), /hue|evil/);
});

test('invalid ids, paths, kinds, and numeric coercions cannot reach the shell', async () => {
  for (const entity of ['light.one;touch /x', 'light.UPPER', 'switch.one', 'light.one\n', '', null, 3]) {
    assert.throws(() => Bridge.buildCommand('/cli', [entity], {kind: Kind.power, on: true}));
  }
  for (const cli of ['relative', '/bad\npath', '/bad\0path', null]) assert.throws(() => Bridge.buildCommand(cli));
  for (const [kind, field, low, high] of [[Kind.colour, 'hue', 0, 360], [Kind.colour, 'saturation', 0, 100],
    [Kind.white, 'kelvin', 2000, 7000], [Kind.brightness, 'brightness', 1, 100]]) {
    for (const value of [low - 1, high + 1, 5.5, String(low), null, true, NaN, Infinity]) {
      const change = {kind, hue: 0, saturation: 100, [field]: value};
      assert.throws(() => Bridge.buildCommand('/cli', ['light.one'], change));
    }
  }
  assert.throws(() => Bridge.buildCommand('/cli', [], {kind: Kind.power, on: true}));
  assert.throws(() => Bridge.buildCommand('/cli', ['light.one'], {kind: Kind.power, on: 'true'}));
  assert.throws(() => Bridge.buildCommand('/cli', ['light.one'], {kind: 'unknown'}));
  let invoked = false;
  const transport = Bridge.create('/cli', () => { invoked = true; });
  assert.equal((await transport.write(['light.one'], {kind: Kind.white, kelvin: '4000'})).ok, false);
  assert.equal(invoked, false);
});

test('bridge uses the injected BTT promise and treats stdout, not exit status, as the contract', async () => {
  const invocations = [];
  const transport = Bridge.create('/cli', async options => {
    invocations.push(options);
    return JSON.stringify(reply([fixture()]));
  });
  assert.deepEqual(await transport.read(), reply([fixture()]));
  assert.deepEqual(invocations[0], {script: "/usr/bin/python3 -B '/cli' lights", launchPath: '/bin/zsh', parameters: '-c'});
  const failure = Bridge.create('/cli', async () => { throw new Error('bridge unavailable'); });
  assert.deepEqual(await failure.read(), {ok: false, error: 'bridge unavailable'});
  assert.deepEqual(Bridge.parseOutput('{"ok":false,"error":"Bulb is unavailable"}'), {ok: false, error: 'Bulb is unavailable'});
  // The dashboard reports "unknown" for a light it hasn't seen yet; that must not sink the reply.
  const unseen = reply([fixture(), fixture('unseen', {available: true, state: 'unknown'})]);
  assert.deepEqual(Bridge.parseOutput(JSON.stringify(unseen)), unseen);
  for (const output of ['', 'oops', 'null', '{}', '{"ok":true}', '{"ok":true,"lights":[{}]}',
    JSON.stringify(reply([fixture(), fixture()])), JSON.stringify(reply([fixture('bad;id')]))]) {
    assert.equal(Bridge.parseOutput(output).ok, false, output);
  }
});

test('opening selects only available lights, uses first mode, fallback saturation, and shared white range', async () => {
  const {model} = harness([fixture('one', {color_mode: 'color_temp', min_color_temp_kelvin: 1800}),
    fixture('two', {min_color_temp_kelvin: 2700, max_color_temp_kelvin: 9000}),
    fixture('missing', {available: false, state: 'unavailable'})]);
  await model.refresh({resetSelection: true});
  assert.deepEqual(model.snapshot().selected, ['light.one', 'light.two']);
  assert.equal(model.snapshot().mode, Kind.white);
  assert.equal(model.snapshot().values.saturation, 100);
  assert.deepEqual(model.snapshot().capabilities.range, {min: 2700, max: 6500});
  model.select('light.missing');
  assert.equal(model.snapshot().selected.length, 2);
  model.select('light.one');
  assert.deepEqual(model.snapshot().selected, ['light.two']);
  assert.equal(model.snapshot().mode, Kind.colour);
  await model.refresh({resetSelection: true});
  assert.equal(model.snapshot().selected.length, 2);
});

test('fractional device values become integer control drafts before keyboard edits', async () => {
  const {model} = harness([fixture('one', {hue: 25.4, saturation: 66.6, brightness: 72.8, color_temp_kelvin: 3456.7})]);
  await model.refresh({resetSelection: true});
  assert.deepEqual(model.snapshot().values, {hue: 25, saturation: 67, brightness: 73, kelvin: 3457, on: true});
});

test('queue serializes writes and keeps only the newest pending change of each kind', async () => {
  const {model, calls} = harness();
  await model.refresh({resetSelection: true});
  model.release(Kind.brightness, {brightness: 70});
  model.release(Kind.colour, {hue: 90, saturation: 40});
  model.release(Kind.white, {kelvin: 4000});
  model.release(Kind.white, {kelvin: 5000});
  model.release(Kind.power, {on: false});
  model.release(Kind.brightness, {brightness: 80});
  model.release(Kind.brightness, {brightness: 90});
  assert.equal(calls.length, 1);
  assert.equal(model.snapshot().status, 'Sending…');
  calls[0].gate.resolve(reply([fixture('one', {brightness: 70})]));
  await turn();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].change, {kind: Kind.white, kelvin: 5000});
  calls[1].gate.resolve(reply([fixture()])); await turn();
  assert.deepEqual(calls[2].change, {kind: Kind.power, on: false});
  calls[2].gate.resolve(reply([fixture()])); await turn();
  assert.deepEqual(calls[3].change, {kind: Kind.brightness, brightness: 90});
  calls[3].gate.resolve(reply([fixture('one', {brightness: 90})])); await turn();
  assert.equal(model.snapshot().busy, false);
  assert.equal(model.snapshot().values.brightness, 90);
});

test('targets are captured at release; refreshes cannot overlap writes; failed writes are never retried', async () => {
  const {model, calls, transport} = harness([fixture(), fixture('two')]);
  await model.refresh({resetSelection: true});
  model.release(Kind.power, {on: false});
  model.release(Kind.white, {kelvin: 3500});
  model.select('light.two');
  let reads = 0;
  transport.read = async () => { reads++; return reply([fixture(), fixture('two')]); };
  const refreshed = model.refresh();
  assert.equal(reads, 0);
  calls[0].gate.resolve({ok: false, error: 'uncertain write'}); await turn();
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].entities, ['light.one', 'light.two']);
  calls[1].gate.resolve({ok: false, error: 'temperature failed'});
  await refreshed;
  assert.equal(reads, 1);
  assert.equal(calls.length, 2);
  assert.equal(model.snapshot().error, '');
  model.release(Kind.power, {on: true});
  calls[2].gate.resolve({ok: false, error: 'power failed'}); await turn();
  assert.equal(model.snapshot().status, 'power failed');
  await turn();
  assert.equal(calls.length, 3);
});

test('reply updates swatches but never overwrites a dragging control', async () => {
  const {model, calls} = harness();
  await model.refresh({resetSelection: true});
  model.release(Kind.power, {on: false});
  model.beginDrag(Kind.colour);
  model.preview(Kind.colour, {hue: 200, saturation: 50});
  model.beginDrag(Kind.brightness);
  model.preview(Kind.brightness, {brightness: 88});
  calls[0].gate.resolve(reply([fixture('one', {hue: 10, brightness: 15})])); await turn();
  assert.equal(model.snapshot().lights[0].hue, 10);
  assert.equal(model.snapshot().values.hue, 200);
  assert.equal(model.snapshot().values.brightness, 88);
  model.cancelDrag(Kind.colour);
  assert.equal(model.snapshot().values.hue, 10);
  assert.equal(model.snapshot().values.brightness, 88);
});

test('unsupported selections and empty kelvin intersections never dispatch a colour write', async () => {
  const {model, calls} = harness([fixture('one', {max_color_temp_kelvin: 2500}),
    fixture('two', {min_color_temp_kelvin: 3000, supports_hue: false})]);
  await model.refresh({resetSelection: true});
  assert.equal(model.snapshot().capabilities.white, false);
  model.release(Kind.white, {kelvin: 4000});
  model.release(Kind.colour, {hue: 10, saturation: 50});
  assert.equal(calls.length, 0);
});

test('generated page waits for BTTInitialize and refreshes on every visible opening', async () => {
  const generated = spawnSync('/usr/bin/python3', ['-B', '-c',
    'from macbook.bettertouchtool.lighting_control.page import build_page; print(build_page([], "/cli"))'],
  {cwd: path.resolve(__dirname, '../..'), encoding: 'utf8'});
  assert.equal(generated.status, 0, generated.stderr);
  const scripts = [...generated.stdout.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
  let mounts = 0;
  let opens = 0;
  let bridges = 0;
  const context = vm.createContext({document: {getElementById: id => id === 'lighting-config' ?
    {textContent: '{"lights":[],"cli_path":"/cli"}'} : {}},
  LightControl: {mount() { mounts++; return {open() { opens++; return Promise.resolve(); }}; }},
  LightingBridge: {create() { bridges++; return {}; }},
  runShellScript() { assert.fail('lifecycle must not invoke the shell on its own'); }});
  vm.runInContext(scripts.at(-1)[1], context);
  assert.equal(mounts, 0);
  context.BTTWindowWillBecomeVisible();
  assert.equal(opens, 0);
  await context.BTTInitialize();
  assert.equal(mounts, 1);
  assert.equal(bridges, 1);
  assert.equal(opens, 1);
  await context.BTTWindowWillBecomeVisible();
  await context.BTTInitialize();
  assert.equal(mounts, 1);
  assert.equal(opens, 3);
});

class Element {
  constructor(document, tag) {
    this.ownerDocument = document; this.tagName = tag; this.children = []; this.attributes = {};
    this.listeners = {}; this.style = {}; this.className = ''; this.textContent = '';
    this.classList = {toggle: () => {}};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(event, handler) { this.listeners[event] = handler; }
  fire(event, properties = {}) { this.listeners[event]?.({preventDefault() {}, ...properties}); }
}
function viewHarness() {
  const document = {createElement: tag => new Element(document, tag)};
  const pickers = [];
  class Picker {
    constructor() {
      this.color = {hsv: {h: 0, s: 100, v: 100}, kelvin: 4000};
      this.listeners = {}; pickers.push(this);
    }
    on(event, handler) { this.listeners[event] = handler; }
    setOptions(options) { this.options = options; }
    fire(event) { this.listeners[event](); }
  }
  const context = vm.createContext({iro: {ColorPicker: Picker, ui: {Wheel() {}, Slider() {}}}});
  for (const file of ['state.js', 'control.js']) vm.runInContext(fs.readFileSync(path.join(folder, file), 'utf8'), context);
  const root = new Element(document, 'main');
  return {root, context, pickers};
}
function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }

test('mounted component sends only on release and keeps white and colour channels separate', async () => {
  const {root, context, pickers} = viewHarness();
  const calls = [];
  const control = context.LightControl.mount(root, {lights: [fixture()], transport: {
    read: async () => reply([fixture()]), write: async (entities, change) => { calls.push({...change}); return reply([fixture()]); }
  }});
  await control.open();
  const [wheel, white] = pickers;
  wheel.fire('input:start');
  wheel.color.hsv = {h: 123.4, s: 42.2, v: 100};
  wheel.fire('input:change');
  assert.equal(calls.length, 0);
  wheel.fire('input:end'); await turn();
  assert.deepEqual(calls[0], {kind: Kind.colour, hue: 123, saturation: 42});
  const nodes = descendants(root);
  nodes.find(node => node.tagName === 'button' && node.textContent === 'White').fire('click');
  white.fire('input:start'); white.color.kelvin = 4200.4; white.fire('input:change');
  assert.equal(calls.length, 1);
  white.fire('input:end'); await turn();
  assert.deepEqual(calls[1], {kind: Kind.white, kelvin: 4200});
  const range = nodes.find(node => node.tagName === 'input');
  range.value = '86'; range.fire('input');
  assert.equal(calls.length, 2);
  range.fire('change'); await turn();
  assert.deepEqual(calls[2], {kind: Kind.brightness, brightness: 86});
  const whiteHost = nodes.filter(node => node.attributes.role === 'slider')[1];
  whiteHost.fire('keydown', {key: 'ArrowRight'});
  assert.equal(calls.length, 3);
  whiteHost.fire('keyup', {key: 'ArrowRight'}); await turn();
  assert.equal(calls[3].kind, Kind.white);
  assert.equal('hue' in calls[3], false);
  assert.match(context.LightControl.swatch(fixture()), /^hsl\(25 100% 50%\)$/);
  assert.match(context.LightControl.swatch(fixture('one', {color_mode: 'color_temp'})), /^rgb\(/);
  control.destroy();
  assert.equal(root.children.length, 0);
});
