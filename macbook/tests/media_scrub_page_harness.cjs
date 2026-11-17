// Loads a built page's scripts into a vm with a stub DOM and drives the BTT lifecycle hooks.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script(?: id="([^"]*)" type="application\/json")?>([\s\S]*?)<\/script>/g)];
const config = scripts.find(match => match[1] === 'scrub-config')[2];
const code = scripts.filter(match => !match[1]).map(match => match[2]);

const listeners = {};
const shell = [];
const document = {hidden: false, getElementById: id => (id === 'scrub-config' ? {textContent: config} : root),
  addEventListener: (name, fn) => { listeners[name] = fn; }};
function element() {
  return {children: [], textContent: '', ownerDocument: document, style: {},
    append(...kids) { this.children.push(...kids); }, addEventListener() {}};
}
document.createElement = element;
const root = element();
const timers = new Set();
let timerId = 0;
const context = vm.createContext({document, console, Uint8Array, btoa, TextEncoder, Promise,
  crypto: {getRandomValues: bytes => bytes.fill(7)},
  setTimeout: () => { const id = ++timerId; timers.add(id); return id; },
  clearTimeout: id => timers.delete(id),
  requestAnimationFrame: () => 1, cancelAnimationFrame() {},
  runShellScript: options => new Promise(resolve => shell.push({script: options.script, resolve}))});
context.globalThis = context;
for (const source of code) vm.runInContext(source, context);
const turn = () => new Promise(resolve => setImmediate(resolve));
const answer = async () => {
  shell.shift().resolve(JSON.stringify({ok: true, source: 'none', state: 'IDLE', title: '', position: 0,
    duration: 0, rate: 1, media_id: null, seekable: false, age: 0, seek_ack: null, error: null}));
  await turn();
};

(async () => {
  assert.equal(shell.length, 0, 'nothing runs before BTTInitialize');
  document.hidden = true;
  context.BTTInitialize();
  assert.equal(shell.length, 0, 'a page that loads hidden builds but does not poll');
  document.hidden = false;
  context.BTTWindowWillBecomeVisible();
  assert.equal(shell.length, 1);
  assert.match(shell[0].script, /^\/usr\/bin\/python3 -B '.*media_scrub\.py' status$/);
  context.BTTWindowWillBecomeVisible();
  listeners.visibilitychange();
  assert.equal(shell.length, 1, 'repeated starts never overlap a status call');
  await answer();
  assert.equal(timers.size, 1, 'the next poll is scheduled after the reply');
  const stops = [() => context.BTTWillHideWindow(), () => context.BTTWillCloseWindow(),
    () => { document.hidden = true; listeners.visibilitychange(); }];
  for (const stop of stops) {
    document.hidden = false;
    context.BTTWindowWillBecomeVisible();
    stop();
    assert.equal(timers.size, 0, 'no poll timer survives a stop');
    while (shell.length) await answer();
    assert.equal(timers.size, 0, 'a reply that lands after the stop schedules nothing');
  }
  document.hidden = false;
  listeners.visibilitychange();
  assert.equal(shell.length, 1, 'becoming visible again resumes polling');
  console.log('ok');
})().catch(error => { console.error(error); process.exit(1); });
