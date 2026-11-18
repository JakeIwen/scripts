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
const RELAUNCH = 'Confirm: The previous run may still be active; this starts another run';

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
  assert.equal(relaunchConfirm({...job, remote_state: 'failed'}, 'run pi-check now'), 'Confirm: run pi-check now');
  assert.equal(relaunchConfirm({...job, remote_state: 'failed'}), undefined);
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
  const plain = render({...data, jobs: [{...job, remote_state: 'failed'}]}).node;
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
