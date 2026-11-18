import assert from 'node:assert/strict';
import test from 'node:test';
import {Element} from './dom_fixture.mjs';

document.addEventListener ??= () => {};
globalThis.Node ??= Element;
document.createElementNS ??= (_namespace, tag) => new Element(tag);
const {agentBody, agentProblemsFromError, validateAgent} = await import('../static/deferred_agent_form.mjs');
const {agentTags, relaunchConfirm} = await import('../static/deferred_agent_job.mjs');
const {addAgentForm, modelControl} = await import('../static/section_deferred_agent.mjs');
const {render, searchItems} = await import('../static/section_deferred.mjs');

const mac = {host: 'mac', due: '2026-11-20T09:30', name: 'check-build', description: '', prompt: 'Check the build.\n',
  pi_prompt_path: '', model: '', effort: 'medium', cwd: '', ready: '', attempts: '', timeout: '', run_timeout: ''};
const vanpi = {...mac, host: 'vanpi', prompt: '', pi_prompt_path: '/home/pi/briefs/can scan.md', model: 'astra', run_timeout: '30'};
const RELAUNCH = 'Confirm: The previous Pi attempt d0123456789abcdef may still be active; this starts another run';

const walk = element => [element, ...(element.childNodes ?? []).flatMap(child => (child instanceof Element ? walk(child) : []))];
const byKey = (root, key) => walk(root).find(element => element.dataset?.key === key);
const text = element => element.textContent;

test('complete Mac and vanpi forms have no problems', () => {
  assert.deepEqual(validateAgent(mac), {});
  assert.deepEqual(validateAgent(vanpi), {});
  assert.deepEqual(validateAgent({...vanpi, pi_prompt_path: '', prompt: 'pasted'}), {});
  assert.deepEqual(validateAgent({...mac, run_timeout: 'ignored on the Mac'}), {});
});

test('each bad field is named', () => {
  const problems = validateAgent({...mac, host: 'pi', due: '', name: 'Bad Name', prompt: '  ', effort: 'huge',
    cwd: 'relative', ready: 'a\nb', attempts: '0', timeout: 'x'});
  assert.deepEqual(Object.keys(problems).sort(), ['attempts', 'cwd', 'due', 'effort', 'host', 'name', 'prompt', 'ready', 'timeout']);
  assert.deepEqual(Object.keys(validateAgent({...vanpi, model: 'haiku', pi_prompt_path: 'relative.md', run_timeout: '0'})).sort(),
    ['model', 'pi_prompt_path', 'run_timeout']);
  assert.deepEqual(Object.keys(validateAgent({...mac, model: 'Sonnet!'})), ['model']);
  assert.match(validateAgent({...vanpi, prompt: 'both'}).prompt, /not both/);
  assert.match(validateAgent({...vanpi, pi_prompt_path: ''}).prompt, /Pi prompt file/);
  assert.equal(validateAgent({...mac, ready: 'x'.repeat(501)}).ready, 'One line, at most 500 characters.');
});

test('the body carries one prompt source, server dates and numbers, and always confirms', () => {
  const body = agentBody({...mac, attempts: '2', timeout: '600', run_timeout: '30'});
  assert.deepEqual(body, {action: 'add-agent', host: 'mac', name: 'check-build', due: '2026-11-20 09:30', description: '',
    effort: 'medium', prompt: 'Check the build.\n', attempts: 2, timeout: 600, confirmed: true});
  const pi = agentBody(vanpi);
  assert.equal(pi.pi_prompt_path, '/home/pi/briefs/can scan.md');
  assert.equal('prompt' in pi, false);
  assert.equal(pi.model, 'astra');
  assert.equal(pi.run_timeout, 30);
  assert.equal(agentBody({...vanpi, pi_prompt_path: '', prompt: 'x'}).prompt, 'x');
});

test('server messages map to the field they name', () => {
  const cases = {
    'Invalid job name': 'name', 'Due time must be YYYY-MM-DD HH:MM': 'due', 'run_timeout must be positive': 'run_timeout',
    'timeout must be positive': 'timeout', 'Pi prompt path must be one absolute path of at most 1024 characters': 'pi_prompt_path',
    'Prompt must be non-blank text without NUL': 'prompt', 'cwd must be the canonical path of an existing folder under your home folder': 'cwd',
    'model must be astra, sol or luna on vanpi, or a Claude model name on the Mac': 'model',
    'ready must be one line of at most 500 characters': 'ready', 'deferred.zsh: job 2026-11-20-x already exists': 'form',
  };
  Object.entries(cases).forEach(([message, field]) => assert.deepEqual(Object.keys(agentProblemsFromError(message)), [field], message));
});

const job = {
  id: '2026-11-20-pi-check', valid: true, name: 'pi-check', due: '2026-11-20T09:00', state: 'failed', attempts: 1,
  max_attempts: 1, timeout: 900, description: 'd', created: '2026-11-19T09:00', origin: '/tmp', last_log: 'Held: …',
  give_up: '2026-11-27T09:00', agent: true, ready: null, last_not_ready: null, not_ready_checks: 0,
  host: 'vanpi', model: 'sol', effort: 'medium', remote_run: null, remote_state: 'launch-unknown',
  remote_token: 'd0123456789abcdef', relaunch_id: 'd0123456789abcdef',
};

test('agent jobs show host, model and effort tags and the Pi run state at its level', () => {
  const tags = agentTags(job);
  assert.match(text(tags), /vanpi.*sol.*effort medium.*Pi run: launch-unknown/);
  const state = tags.childNodes.find(child => /Pi run/.test(text(child)));
  assert.equal(state.className, 'tag level-error');
  const running = agentTags({...job, remote_state: 'running', remote_run: '1120-090000-abcd-d0123456789abcdef'});
  assert.equal(running.childNodes.find(child => /Pi run/.test(text(child))).className, 'tag level-ok');
  assert.doesNotMatch(text(agentTags({...job, host: 'mac', model: 'haiku', remote_state: null})), /Pi run/);
  assert.match(text(agentTags({...job, host: 'mac', model: 'haiku', remote_state: null})), /This Mac/);
  assert.equal(agentTags({...job, agent: false, host: null}), null);
});

test('retrying an unknown launch says it may start a second run', () => {
  assert.equal(relaunchConfirm(job, 'run pi-check now'), RELAUNCH);
  const settled = {...job, remote_state: 'failed', relaunch_id: null};
  assert.equal(relaunchConfirm(settled, 'run pi-check now'), 'Confirm: run pi-check now');
  assert.equal(relaunchConfirm(settled), undefined);
  assert.match(relaunchConfirm({...settled, remote_state: 'failed', relaunch_id: '1120-090000-abcd-d0123456789abcdef'}),
    /1120-090000-abcd-d0123456789abcdef may still be active/);
  const data = {checker: {agent_loaded: true, stale: false, last_run: null}, jobs_dir: '/j', tasks: [], jobs: [job]};
  const {node} = render(data);
  const retry = byKey(node, `retry-${job.id}`);
  retry.click();
  assert.equal(text(retry), RELAUNCH);
  const runNow = byKey(node, `run-${job.id}`);
  runNow.click();
  assert.equal(text(runNow), RELAUNCH);
  assert.match(text(node), /Pi run: launch-unknown/);
  assert.equal(byKey(node, `abandon-${job.id}`), undefined);
  const plain = render({...data, jobs: [{...job, remote_state: 'failed', relaunch_id: null}]}).node;
  const ordinary = byKey(plain, `retry-${job.id}`);
  assert.equal(ordinary.classes.has('armed'), false);
});

test('the model control follows the host and vanpi-only rows hide on the Mac', () => {
  assert.equal(modelControl('vanpi').tagName, 'SELECT');
  assert.deepEqual(modelControl('vanpi').childNodes.map(option => option.value), ['astra', 'sol', 'luna']);
  assert.equal(modelControl('mac').tagName, 'INPUT');
  const form = addAgentForm();
  const host = byKey(form, 'agent-host');
  const runTimeoutRow = walk(form).find(element => element.className === 'setting' && byKey(element, 'agent-run_timeout'));
  assert.equal(runTimeoutRow.hidden, true);
  assert.equal(byKey(form, 'agent-model').tagName, 'INPUT');
  host.value = 'vanpi';
  host.dispatchEvent(new Event('change'));
  assert.equal(runTimeoutRow.hidden, false);
  assert.equal(byKey(form, 'agent-model').tagName, 'SELECT');
  assert.equal(byKey(form, 'agent-model').value, 'sol');
});

test('search offers the agent form', () => {
  const items = searchItems({jobs: []});
  assert.ok(items.some(item => item.title === 'Add an agent job' && item.anchor === 'add-agent-job'));
});

test('both add forms stop names at the length whose dated id the CLI accepts', async () => {
  const {NAME_LIMIT, validateAdd} = await import('../static/deferred_form.mjs');
  assert.equal(NAME_LIMIT, 60);
  assert.deepEqual(validateAgent({...mac, name: 'n'.repeat(60)}), {});
  assert.ok(validateAgent({...mac, name: 'n'.repeat(61)}).name);
  const script = {due: '2026-11-06T09:30', name: 'n'.repeat(61), description: '', script: '#!/bin/zsh\n', attempts: '', timeout: ''};
  assert.ok(validateAdd(script).name);
  assert.deepEqual(validateAdd({...script, name: 'n'.repeat(60)}), {});
});

test('a vanpi timeout below what one check needs is named', () => {
  assert.match(validateAgent({...vanpi, timeout: '389'}).timeout, /at least 390/);
  assert.deepEqual(validateAgent({...vanpi, timeout: '390'}), {});
  assert.deepEqual(validateAgent({...mac, timeout: '60'}), {});
});

test('a vanpi timeout needs the floor, and the ready allowance more with a ready condition', async () => {
  const {PI_MIN_TIMEOUT, PI_READY_ALLOWANCE} = await import('../static/deferred_agent_form.mjs');
  assert.equal(PI_READY_ALLOWANCE, 30);
  const floor = PI_MIN_TIMEOUT + PI_READY_ALLOWANCE;
  const withReady = {...vanpi, ready: 'true'};
  assert.match(validateAgent({...withReady, timeout: String(floor - 1)}).timeout, /with a ready condition, at least 420/);
  assert.deepEqual(validateAgent({...withReady, timeout: String(floor)}), {});
  assert.deepEqual(validateAgent({...vanpi, timeout: String(PI_MIN_TIMEOUT)}), {});
  assert.deepEqual(validateAgent({...mac, ready: 'true', timeout: '60'}), {});
});

test('a vanpi job has no attempts: the form hides the row and the body omits it; the Mac keeps it', () => {
  assert.equal('attempts' in agentBody({...vanpi, attempts: '3'}), false);
  assert.equal(agentBody({...mac, attempts: '3'}).attempts, 3);
  assert.deepEqual(validateAgent({...vanpi, attempts: 'x'}), {});
  const form = addAgentForm();
  const row = walk(form).find(element => element.className === 'setting' && byKey(element, 'agent-attempts'));
  assert.equal(row.hidden, false);
  const host = byKey(form, 'agent-host');
  host.value = 'vanpi';
  host.dispatchEvent(new Event('change'));
  assert.equal(row.hidden, true);
  host.value = 'mac';
  host.dispatchEvent(new Event('change'));
  assert.equal(row.hidden, false);
});

test('Run now and Retry send the unresolved attempt id; a settled job sends none', async () => {
  const {relaunchFields} = await import('../static/deferred_agent_job.mjs');
  const run = '1120-090000-abcd-d0123456789abcdef';
  assert.deepEqual(relaunchFields({...job, relaunch_id: run}), {confirmed: true, relaunch: run});
  assert.deepEqual(relaunchFields({...job, remote_state: 'running', relaunch_id: null}), {});
  assert.deepEqual(relaunchFields({host: 'mac', relaunch_id: null}), {});
});

test('a still-running Pi run-now is not a failure, and the card shows it only while the run is active', async () => {
  const {collectedLaterOnCard, describeRunNow, collectedLaterText} = await import('../static/deferred_agent_job.mjs');
  const pending = {exit_status: 4, message: 'running 1120-x: its Pi run is going'};
  assert.equal(describeRunNow(pending), collectedLaterText);
  // The CLI's own not-ready line says when the checker tries again; a relaunch prints what it cleared first.
  const notReady = 'not ready 1120-x; the checker retries from its due time 2026-11-20 09:00; output in /j/last.log';
  assert.equal(describeRunNow({exit_status: 4, message: notReady}), notReady);
  assert.equal(describeRunNow({exit_status: 4, message: 'Cleared the unresolved attempt d1; the next launch starts a new run.\n'
    + 'running 1120-x: its Pi run is still going'}), collectedLaterText);
  assert.equal(describeRunNow({exit_status: 4, message: 'Tool exited 4; refresh its state before retrying'}), undefined);
  assert.equal(describeRunNow({exit_status: 1, message: 'running x'}), undefined);
  assert.equal(describeRunNow({exit_status: 3, message: notReady}), undefined);
  assert.equal(collectedLaterOnCard({host: 'vanpi', remote_state: 'running'}, pending), true);
  assert.equal(collectedLaterOnCard({host: 'vanpi', remote_state: 'launching'}, pending), true);
  assert.equal(collectedLaterOnCard({host: 'vanpi', remote_state: 'finished'}, pending), false);
  assert.equal(collectedLaterOnCard({host: 'vanpi', remote_state: 'failed'}, pending), false);
  assert.equal(collectedLaterOnCard({host: 'mac', remote_state: null}, pending), false);
});

const data = jobs => ({checker: {agent_loaded: true, stale: false, last_run: null}, jobs_dir: '/j', tasks: [], jobs});

test('Reschedule is withheld from a vanpi job that has launched, with the reason in an info popover', () => {
  const unlaunched = {...job, state: 'pending', remote_state: 'unlaunched', relaunch_id: null};
  assert.ok(byKey(render(data([unlaunched])).node, `reschedule-${job.id}`));
  const macJob = {...job, host: 'mac', remote_state: null, relaunch_id: null};
  assert.ok(byKey(render(data([macJob])).node, `reschedule-${job.id}`));
  ['launching', 'launch-unknown', 'running', 'finished', 'failed'].forEach(remote_state => {
    const node = render(data([{...unlaunched, remote_state}])).node;
    assert.equal(byKey(node, `reschedule-${job.id}`), undefined, remote_state);
    assert.match(text(node), /cannot be rescheduled/);
  });
});

test('a stale "still running" receipt is not shown once the job is no longer running', () => {
  const task = {id: 't1', job: job.id, state: 'done', exit_status: 4, message: 'running x', started_at: 1, finished_at: 2};
  const failed = {...job, remote_state: 'failed', relaunch_id: null};
  assert.doesNotMatch(text(render({...data([failed]), tasks: [task]}).node), /still running on vanpi/);
  const running = {...job, state: 'pending', remote_state: 'running', relaunch_id: null};
  assert.match(text(render({...data([running]), tasks: [task]}).node), /still running on vanpi/);
});

test('a resumed run-now task reports with the Pi-aware wording; a 409 on retry shows its message and reloads', async () => {
  const {section} = await import('./dom_fixture.mjs');
  const {register} = await import('../static/panel_sections.mjs');
  const root = section('deferred');
  let loads = 0;
  const finished = {id: 't9', job: job.id, state: 'done', exit_status: 4, message: 'running x', started_at: 1, finished_at: 2};
  const running = {...finished, state: 'running', finished_at: null};
  register([{id: 'deferred', endpoint: '/api/deferred', render: value => { loads++; return {node: new Element(), summary: '', level: 'ok', value}; }}]);
  globalThis.fetch = async (path, init) => {
    if (init.method === 'POST') {
      return {ok: false, status: 409, json: async () => ({error: 'The Pi attempt x may still be active', code: 'relaunch-unconfirmed'})};
    }
    return {ok: true, status: 200, json: async () => (path.startsWith('/api/tasks/') ? finished : data([]))};
  };
  const realTimeout = globalThis.setTimeout;
  // Only the 2 s poll is shortened; the notice's own clearing timer keeps its delay.
  globalThis.setTimeout = (fn, ms) => realTimeout(fn, ms === 2000 ? 0 : ms);
  try {
    const {node} = render({...data([{...job, state: 'pending', remote_state: 'running', relaunch_id: null}]), tasks: [running]});
    await new Promise(resolve => realTimeout(resolve, 50));
    assert.match(root.parts['.notice'].textContent, /pi-check: still running on vanpi; a later check collects it/);
    const retry = byKey(render(data([job])).node, `retry-${job.id}`);
    retry.click();
    retry.click();
    await new Promise(resolve => realTimeout(resolve, 50));
    assert.match(root.parts['.notice'].textContent, /may still be active/);
    assert.ok(loads >= 2, 'the section reloaded after the 409');
    assert.ok(node);
  } finally {
    globalThis.setTimeout = realTimeout;
  }
});

test('Abandon belongs to the Pi attempt whose stop was refused, and the request names that attempt', async () => {
  const {section} = await import('./dom_fixture.mjs');
  const {register} = await import('../static/panel_sections.mjs');
  section('deferred');
  register([{id: 'deferred', endpoint: '/api/deferred', render: () => ({node: new Element(), summary: '', level: 'ok'})}]);
  const first = 'd0123456789abcdef';
  const running = {...job, state: 'pending', remote_state: 'running', remote_run: `1120-090000-abcd-${first}`,
    remote_token: first, relaunch_id: null};
  const posts = [];
  globalThis.fetch = async (path, init) => {
    if (init.method === 'POST') {
      posts.push(JSON.parse(init.body));
      return {ok: false, status: 409, json: async () => ({error: `Could not read run ${first}`, code: 'remote-active'})};
    }
    return {ok: true, status: 200, json: async () => data([])};
  };
  const settle = () => new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(byKey(render(data([running])).node, `abandon-${job.id}`), undefined);
  const cancel = byKey(render(data([running])).node, `cancel-${job.id}`);
  cancel.click();
  cancel.click();
  await settle();
  const abandon = byKey(render(data([running])).node, `abandon-${job.id}`);
  abandon.click();
  assert.match(text(abandon), new RegExp(`leave Pi attempt ${first} alone`));
  abandon.click();
  await settle();
  assert.deepEqual(posts.at(-1), {action: 'cancel', id: job.id, confirmed: true, abandon: true, attempt: first});
  const second = 'dfedcba9876543210';
  const replaced = {...running, remote_run: `1120-100000-abcd-${second}`, remote_token: second};
  assert.equal(byKey(render(data([replaced])).node, `abandon-${job.id}`), undefined);
  assert.ok(byKey(render(data([replaced])).node, `cancel-${job.id}`));
});
