const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const folder = path.resolve(__dirname, '../bettertouchtool/media_scrub_control');
const Bridge = require(path.join(folder, 'bridge.js'));
const Controller = require(path.join(folder, 'controller.js'));
const View = require(path.join(folder, 'view.js'));

const PAGE = 'pagetest01';
const turn = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return {promise, resolve};
}
const sample = (extra = {}) => ({ok: true, source: 'vlc', state: 'PLAYING', title: 'A Movie', position: 100,
  duration: 1000, rate: 1, media_id: 'vlc:item:7', seekable: true, age: 0, seek_ack: null, ...extra});

// A controller on fake time: timers and frames run only when the test says so.
function harness(options = {}) {
  const clock = {now: 1_000_000};
  const timeouts = new Map(), frames = new Map();
  let next = 1;
  const statusCalls = [], seekCalls = [], views = [];
  const transport = {
    status: () => { const gate = deferred(); statusCalls.push(gate); return gate.promise; },
    seek: request => { const gate = deferred(); seekCalls.push({request, gate}); return gate.promise; },
  };
  const controller = Controller.create({transport, pageId: PAGE, view: {update: state => views.push(state)},
    timers: {now: () => clock.now,
      setTimer: (fn, ms) => { const id = next++; timeouts.set(id, {fn, ms}); return id; },
      clearTimer: id => timeouts.delete(id),
      raf: fn => { const id = next++; frames.set(id, fn); return id; },
      caf: id => frames.delete(id)}});
  const h = {clock, timeouts, frames, statusCalls, seekCalls, views, controller,
    last: () => views[views.length - 1],
    async reply(value, index = h.statusCalls.length - 1) { h.statusCalls[index].resolve(value); await turn(); },
    runTimers() { const due = [...timeouts.entries()]; timeouts.clear(); due.forEach(([, t]) => t.fn()); },
    runFrames() { const due = [...frames.values()]; frames.clear(); due.forEach(fn => fn()); },
    async seekResult(index, value) { h.seekCalls[index].gate.resolve(value); await turn(); }};
  return h;
}
async function playing(extra = {}) {
  const h = harness();
  h.controller.start();
  await h.reply(sample(extra));
  return h;
}

test('polls every 500 ms, never overlapping, and only while started', async () => {
  const h = harness();
  h.controller.start();
  assert.equal(h.statusCalls.length, 1);
  h.controller.start();
  assert.equal(h.statusCalls.length, 1, 'a second start joins the running poll');
  assert.equal(h.timeouts.size, 0, 'no next poll is scheduled while one is in flight');
  await h.reply(sample());
  assert.deepEqual([...h.timeouts.values()].map(t => t.ms), [Controller.POLL_MS]);
  h.runTimers();
  assert.equal(h.statusCalls.length, 2);
  assert.equal(h.timeouts.size, 0);
  h.controller.stop();
  await h.reply(sample());
  assert.equal(h.timeouts.size, 0, 'a reply that outlives stop schedules nothing');
  h.runTimers();
  assert.equal(h.statusCalls.length, 2);
});

test('stop cancels the poll timer and animation frame; a start during a pending call does not overlap', async () => {
  const h = await playing();
  assert.equal(h.timeouts.size, 1);
  assert.equal(h.frames.size, 1);
  h.controller.stop();
  assert.equal(h.timeouts.size, 0);
  assert.equal(h.frames.size, 0);
  h.controller.start();
  assert.equal(h.statusCalls.length, 2);
  h.controller.stop();
  h.controller.start();
  assert.equal(h.statusCalls.length, 2, 'restart while the call is pending reuses it');
  await h.reply(sample());
  assert.equal(h.timeouts.size, 1);
});

test('position extrapolates by rate while PLAYING, clamps to duration, and freezes when paused', async () => {
  const h = await playing({position: 100, rate: 2, age: 0.5});
  assert.equal(h.last().value, 101, 'age counts as elapsed playback');
  h.clock.now += 1000;
  h.runFrames();
  assert.equal(h.last().value, 103);
  assert.equal(h.last().elapsed, '1:43');
  assert.equal(h.last().remaining, '-14:57');
  h.runTimers();
  await h.reply(sample({position: 999, rate: 2}));
  h.clock.now += 1500;
  h.runFrames();
  assert.equal(h.last().value, 1000, 'clamped to the duration');
  h.runTimers();
  await h.reply(sample({state: 'PAUSED', position: 300}));
  assert.equal(h.frames.size, 0, 'no frames while paused');
  h.clock.now += 1000;
  h.controller.render();
  assert.equal(h.last().value, 300);
});

test('a stalled status call lets the last sample go stale: disabled, extrapolation stops', async () => {
  const h = await playing({position: 100, age: 0.5});
  h.clock.now += 1000;
  h.runFrames();
  assert.equal(h.last().disabled, false);
  assert.equal(h.last().value, 101.5);
  h.clock.now += 5000; // the next status call never returns
  h.runFrames();
  assert.equal(h.last().disabled, true);
  assert.equal(h.last().value, 102, 'frozen where freshness ran out (age 0.5 + 1.5 s)');
  assert.equal(h.frames.size, 0, 'no more frames');
  h.controller.dragMove(10);
  h.controller.release(10);
  assert.equal(h.seekCalls.length, 0);
});

test('slider is disabled and extrapolation stops when the sample is not seekable', async () => {
  const h = await playing({seekable: false, position: 50});
  assert.equal(h.last().disabled, true);
  assert.equal(h.frames.size, 0);
  h.controller.dragMove(10);
  h.controller.release(10);
  assert.equal(h.seekCalls.length, 0);
  h.runTimers();
  await h.reply(sample());
  assert.equal(h.last().disabled, false);
  assert.equal(h.frames.size, 1);
  h.runTimers();
  await h.reply({ok: false, error: 'x'});
  assert.equal(h.last().disabled, true);
  assert.equal(h.last().title, 'Status unavailable');
  assert.equal(h.frames.size, 0);
});

test('dragging moves only the thumb; release sends one seek with page and seq', async () => {
  const h = await playing();
  h.controller.dragMove(400);
  h.runTimers();
  await h.reply(sample({position: 110}));
  assert.equal(h.last().value, 400, 'polling never moves the thumb under the finger');
  assert.equal(h.seekCalls.length, 0);
  h.controller.release(400);
  assert.deepEqual(h.seekCalls.map(c => c.request), [
    {page: PAGE, seq: 1, position: 400, source: 'vlc', media_id: 'vlc:item:7'}]);
  assert.equal(h.last().value, 400, 'optimistic hold');
});

test('one seek in flight plus the latest pending target; older results never touch newer optimism', async () => {
  const h = await playing();
  h.controller.release(200);
  h.controller.release(300);
  h.controller.release(400);
  assert.equal(h.seekCalls.length, 1, 'only one CLI at a time');
  await h.seekResult(0, {ok: true, message: ''});
  assert.equal(h.seekCalls.length, 2);
  assert.deepEqual([h.seekCalls[1].request.seq, h.seekCalls[1].request.position], [3, 400], 'seq 2 was replaced');
  assert.equal(h.last().value, 400);
  // seq 1's ack arrives late: it is not the current seq, so the hold stays.
  h.runTimers();
  await h.reply(sample({position: 200, seek_ack: {page: PAGE, seq: 1, ok: true, message: ''}}));
  assert.equal(h.last().value, 400);
  await h.seekResult(1, {ok: true, message: ''});
  h.runTimers();
  await h.reply(sample({position: 401, seek_ack: {page: PAGE, seq: 3, ok: true, message: ''}}));
  assert.equal(Math.round(h.last().value), 401, 'ack for the current seq adopts the live sample');
});

test('the hold is cleared only by an ack for the current seq (other page ids are ignored)', async () => {
  const h = await playing();
  h.controller.release(500);
  await h.seekResult(0, {ok: true, message: ''});
  for (const ack of [{page: 'other', seq: 1, ok: true, message: ''}, null]) {
    h.runTimers();
    await h.reply(sample({position: 100, seek_ack: ack}));
    assert.equal(h.last().value, 500);
  }
  h.runTimers();
  await h.reply(sample({position: 500, seek_ack: {page: PAGE, seq: 1, ok: true, message: ''}}));
  assert.equal(h.last().value, 500);
  h.clock.now += 1000;
  h.controller.render();
  assert.equal(h.last().value, 501, 'now following the live sample');
});

test('a CLI ok:false clears the hold at once with its message; no ack is awaited', async () => {
  for (const result of [{ok: false, message: 'superseded'}, {ok: false, message: ''}]) {
    const h = await playing();
    h.controller.release(500);
    await h.seekResult(0, result);
    assert.notEqual(h.last().value, 500);
    assert.equal(Math.round(h.last().value), 100);
    assert.equal(h.last().message, result.message || 'Seek failed');
  }
});

test('a rejected or garbled seek call also reverts immediately', async () => {
  const h = await playing();
  h.controller.release(500);
  h.seekCalls[0].gate.resolve(undefined);
  await turn();
  assert.equal(Math.round(h.last().value), 100);
  assert.equal(h.last().message, 'Seek failed');
});

test('a failed older seek is ignored while a newer one is pending and the newer one still goes', async () => {
  const h = await playing();
  h.controller.release(200);
  h.controller.release(300);
  await h.seekResult(0, {ok: false, message: 'stale'});
  assert.equal(h.last().message, '');
  assert.equal(h.last().value, 300);
  assert.equal(h.seekCalls[1].request.seq, 2);
  await h.seekResult(1, {ok: false, message: 'refused'});
  assert.equal(h.last().message, 'refused');
});

test('an ack with ok:false reverts and shows its message; a changed track reverts too', async () => {
  const h = await playing();
  h.controller.release(500);
  await h.seekResult(0, {ok: true, message: ''});
  h.runTimers();
  await h.reply(sample({seek_ack: {page: PAGE, seq: 1, ok: false, message: 'Pi refused'}}));
  assert.equal(h.last().message, 'Pi refused');
  assert.equal(Math.round(h.last().value), 100);
  h.controller.release(600);
  await h.seekResult(1, {ok: true, message: ''});
  h.runTimers();
  await h.reply(sample({media_id: 'vlc:item:8', position: 5}));
  assert.equal(h.last().message, 'Track changed');
  assert.equal(Math.round(h.last().value), 5);
});

test('release after the track changed mid-drag does not seek the new track', async () => {
  const h = await playing();
  h.controller.dragMove(300);
  h.runTimers();
  await h.reply(sample({media_id: 'vlc:item:9'}));
  h.controller.release(300);
  assert.equal(h.seekCalls.length, 0);
  assert.equal(h.last().message, 'Track changed');
});

test('an unconfirmed hold times out after a bounded wait', async () => {
  const h = await playing();
  h.controller.release(500);
  await h.seekResult(0, {ok: true, message: ''});
  h.clock.now += Controller.ACK_TIMEOUT_MS + 1;
  h.controller.render();
  assert.equal(h.last().message, 'Seek not confirmed');
  assert.notEqual(h.last().value, 500);
});

test('messages expire', async () => {
  const h = await playing();
  h.controller.release(500);
  await h.seekResult(0, {ok: false, message: 'nope'});
  h.clock.now += 3500;
  h.controller.render();
  assert.equal(h.last().message, '');
});

test('clock formatting', () => {
  assert.equal(Controller.formatClock(0), '0:00');
  assert.equal(Controller.formatClock(65.9), '1:05');
  assert.equal(Controller.formatClock(3725), '1:02:05');
  assert.equal(Controller.formatClock(NaN), '--:--');
});

// Bridge
const CLI = "/some path/it's/media_scrub.py";
test('commands quote the CLI path; the seek token is the base64 of the JSON request alone', () => {
  assert.equal(Bridge.statusCommand(CLI), "/usr/bin/python3 -B '/some path/it'\\''s/media_scrub.py' status");
  const request = {page: PAGE, seq: 3, position: 12.5, source: 'vantv', media_id: "vantv:1:it's \"$(rm -rf /)\" `x` é\n"};
  const command = Bridge.seekCommand(CLI, request);
  const match = command.match(/^\/usr\/bin\/python3 -B '.*' seek --b64 '([A-Za-z0-9+/=]+)'$/);
  assert.ok(match, command);
  assert.deepEqual(JSON.parse(Buffer.from(match[1], 'base64').toString('utf8')), request);
  assert.doesNotMatch(command.split(' seek ')[1], /[$`\\"\n]|rm -rf/);
});

test('invalid requests and paths never reach the shell', async () => {
  const good = {page: PAGE, seq: 0, position: 0, source: 'vlc', media_id: 'm'};
  for (const change of [{page: 'bad page'}, {page: ''}, {page: 'a'.repeat(65)}, {seq: -1}, {seq: 1.5},
    {position: -1}, {position: NaN}, {position: Infinity}, {position: '5'}, {source: 'sh'}, {media_id: ''},
    {media_id: 'x'.repeat(513)}, {media_id: null}]) {
    assert.throws(() => Bridge.seekCommand(CLI, {...good, ...change}), /Invalid seek request/, JSON.stringify(change));
  }
  for (const cli of ['relative', '/bad\npath', '/bad\0', null]) assert.throws(() => Bridge.statusCommand(cli));
  let invoked = false;
  const transport = Bridge.create(CLI, () => { invoked = true; });
  assert.deepEqual(await transport.seek({...good, position: -3}), {ok: false, message: 'Seek failed'});
  assert.equal(invoked, false);
});

test('transport runs fixed zsh -c commands and validates output', async () => {
  const calls = [];
  const output = {value: JSON.stringify({ok: true, source: 'vlc', state: 'PLAYING', title: 'T', position: 5,
    duration: 50, rate: 1.5, media_id: 'vlc:item:1', seekable: true, age: 0.2,
    seek_ack: {page: PAGE, seq: 2, ok: true, message: 'done'}, error: null})};
  const transport = Bridge.create(CLI, async options => { calls.push(options); return output.value; });
  const status = await transport.status();
  assert.equal(calls[0].launchPath, '/bin/zsh');
  assert.equal(calls[0].parameters, '-c');
  assert.equal(calls[0].script, Bridge.statusCommand(CLI));
  assert.deepEqual(status.seek_ack, {page: PAGE, seq: 2, ok: true, message: 'done'});
  assert.equal(status.rate, 1.5);
  for (const bad of ['', 'not json', '[]', '{"ok":"yes"}', null]) {
    output.value = bad;
    assert.equal((await transport.status()).ok, false, String(bad));
  }
  output.value = '{"ok":false,"superseded":true,"message":"superseded"}\n';
  assert.deepEqual(await transport.seek({page: PAGE, seq: 1, position: 1, source: 'vlc', media_id: 'm'}),
    {ok: false, message: 'superseded'});
  output.value = 'warning\n{"ok":true,"superseded":false,"message":""}\n';
  assert.equal((await transport.seek({page: PAGE, seq: 1, position: 1, source: 'vlc', media_id: 'm'})).ok, true);
  const failing = Bridge.create(CLI, async () => { throw new Error('boom'); });
  assert.equal((await failing.status()).ok, false);
  assert.equal((await failing.seek({page: PAGE, seq: 1, position: 1, source: 'vlc', media_id: 'm'})).ok, false);
});

// View with a minimal fake DOM: markup properties are tracked so a test can prove none is used.
function fakeDocument() {
  const document = {createElement: tag => {
    const node = {tag, children: [], listeners: {}, _text: '', ownerDocument: document,
      append(...kids) { this.children.push(...kids); },
      addEventListener(name, fn) { this.listeners[name] = fn; }};
    Object.defineProperty(node, 'innerHTML', {set() { throw new Error('innerHTML used'); }});
    Object.defineProperty(node, 'textContent', {get() { return this._text; },
      set(value) { this._text = String(value); if (value === '') this.children = []; }});
    return node;
  }};
  return document;
}
test('view renders titles and times through textContent only and forwards slider events', () => {
  const document = fakeDocument();
  const container = document.createElement('main');
  const events = [];
  const view = View.mount(container, {input: v => events.push(['input', v]), change: v => events.push(['change', v])});
  const [wrapper] = container.children;
  const [row, line] = wrapper.children;
  const [elapsed, range, remaining] = row.children;
  const hostile = '<img src=x onerror=alert(1)>';
  view.update({disabled: false, max: 100, value: 40, elapsed: '0:40', remaining: '-1:00', title: hostile, message: ''});
  assert.equal(line.textContent, hostile);
  assert.equal([elapsed.textContent, remaining.textContent].join('|'), '0:40|-1:00');
  assert.equal(range.disabled, false);
  assert.equal(range.max, '100');
  assert.equal(range.value, '40');
  view.update({disabled: true, max: 0, value: 0, elapsed: '--:--', remaining: '--:--', title: 'T', message: 'Seek failed'});
  assert.equal(line.textContent, 'Seek failed');
  assert.equal(range.disabled, true);
  range.value = '12';
  range.listeners.input();
  range.listeners.change();
  assert.deepEqual(events, [['input', 12], ['change', 12]]);
});
