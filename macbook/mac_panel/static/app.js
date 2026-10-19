'use strict';
let token = document.querySelector('meta[name="panel-token"]').content;
const sectionNames = ['settings', 'deferred', 'services', 'crontab', 'llm-restore', 'guard', 'ntfy'];
const busy = new Set();
const taskStates = {running: 'running', done: 'done', failed: 'failed'};

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined && text !== null) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}
function notice(section, text, error = false) {
  const target = document.querySelector(`#${section} .notice`);
  target.textContent = text;
  target.classList.toggle('error', error);
}
function stamp(value) {
  if (value === null || value === undefined || value === '') return 'Not recorded';
  const date = new Date(typeof value === 'number' ? value * 1000 : value.replace(' ', 'T'));
  if (Number.isNaN(date.valueOf())) return String(value);
  const seconds = Math.round((Date.now() - date.valueOf()) / 1000);
  const [amount, unit] = Math.abs(seconds) < 60 ? [Math.abs(seconds), 's'] :
    Math.abs(seconds) < 3600 ? [Math.floor(Math.abs(seconds) / 60), 'min'] :
    Math.abs(seconds) < 86400 ? [Math.floor(Math.abs(seconds) / 3600), 'h'] :
    [Math.floor(Math.abs(seconds) / 86400), 'd'];
  return `${date.toLocaleString()} (${seconds < 0 ? 'in ' : ''}${amount}${unit}${seconds >= 0 ? ' ago' : ''})`;
}
function details(title, text, key) {
  const result = node('details');
  if (key) result.dataset.key = key;
  result.append(node('summary', title), node('pre', text));
  return result;
}
function table(headers, rows) {
  const wrapper = node('div', null, 'table-wrap');
  const result = node('table');
  const head = node('tr');
  headers.forEach(text => head.append(node('th', text)));
  const thead = node('thead'); thead.append(head); result.append(thead);
  const body = node('tbody');
  rows.forEach(cells => {
    const row = node('tr');
    cells.forEach(value => {
      const cell = node('td');
      cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—')));
      row.append(cell);
    });
    body.append(row);
  });
  result.append(body); wrapper.append(result); return wrapper;
}
function button(text, action, confirmation) {
  const result = node('button', text); result.type = 'button';
  let armed = false;
  result.addEventListener('click', async () => {
    if (confirmation && !armed) {
      armed = true; result.textContent = confirmation; result.classList.add('armed');
      setTimeout(() => { armed = false; result.textContent = text; result.classList.remove('armed'); }, 8000);
      return;
    }
    result.disabled = true;
    try { await action(); } finally { result.disabled = false; }
  });
  return result;
}
async function renewToken() {
  const response = await fetch('/', {cache: 'no-store'});
  if (!response.ok) throw new Error('Panel is not responding');
  const page = new DOMParser().parseFromString(await response.text(), 'text/html');
  token = page.querySelector('meta[name="panel-token"]').content;
}
async function api(path, body, retry = true) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
    headers: {'X-Panel-Token': token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (response.status === 403 && body === undefined && retry) {
    await renewToken(); return api(path, undefined, false);
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
async function refresh(section) {
  if (busy.has(section)) return;
  busy.add(section);
  try {
    const value = await api(`/api/${section}`);
    const content = document.querySelector(`#${section} .content`);
    const open = new Set([...content.querySelectorAll('details[open]')].map(x => x.dataset.key));
    content.replaceChildren(renderers[section](value));
    content.querySelectorAll('details').forEach(x => { x.open = open.has(x.dataset.key); });
  } catch (error) { notice(section, error.message, true); }
  finally { busy.delete(section); }
}
async function action(section, path, body, after) {
  if (busy.has(section)) { notice(section, 'Section is busy; try again shortly', true); return; }
  busy.add(section); notice(section, 'Working…');
  try {
    const result = await api(path, body);
    notice(section, result.message || 'Completed');
    if (after) await after(result);
    return result;
  } catch (error) { notice(section, error.message, true); }
  finally { busy.delete(section); await refresh(section); }
}
function settingsForm(tool) {
  const {declaration, state} = tool;
  const form = node('form', null, 'tool');
  form.append(node('h3', declaration.title), node('p', declaration.description));
  const inputs = new Map();
  declaration.settings.forEach(setting => {
    const row = node('div', null, 'setting');
    const id = `${declaration.tool}-${setting.name}`;
    const label = node('label', setting.name); label.htmlFor = id;
    const input = node(setting.type === 'choice' ? 'select' : 'input'); input.id = id;
    if (setting.type === 'choice') {
      setting.choices.forEach(value => { const option = node('option', value); option.value = value; input.append(option); });
      input.value = state.values[setting.name];
    } else if (setting.type === 'boolean') {
      input.type = 'checkbox'; input.checked = state.values[setting.name];
    } else {
      input.type = 'number'; input.value = state.values[setting.name]; input.required = true;
      input.step = setting.step ?? (setting.type === 'integer' ? 1 : 'any');
      if (setting.minimum !== null && setting.minimum !== undefined) input.min = setting.minimum;
      if (setting.maximum !== null && setting.maximum !== undefined) input.max = setting.maximum;
    }
    inputs.set(setting.name, input);
    const control = node('div', null, 'control'); control.append(input, node('span', setting.unit || ''));
    row.append(label, control, node('small', `${setting.description} Default: ${setting.default}`, 'help'));
    form.append(row);
  });
  form.append(node('p', state.path, 'muted'));
  if (state.error) form.append(node('p', state.error, 'error'));
  const controls = node('div', null, 'actions');
  const save = node('button', 'Save'); save.type = 'submit'; controls.append(save);
  controls.append(button('Reset to defaults', () => action('settings', '/api/settings', {
    tool: declaration.tool, values: Object.fromEntries(declaration.settings.map(s => [s.name, s.default])), revision: state.revision,
  }), 'Confirm reset to defaults'));
  form.append(controls);
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (!form.reportValidity()) return;
    const values = Object.fromEntries(declaration.settings.map(s => {
      const input = inputs.get(s.name);
      return [s.name, s.type === 'boolean' ? input.checked :
        ['integer', 'number'].includes(s.type) ? Number(input.value) : input.value];
    }));
    save.disabled = true;
    try { await action('settings', '/api/settings', {tool: declaration.tool, values, revision: state.revision}); }
    finally { save.disabled = false; }
  });
  return form;
}
function renderSettings(data) {
  const root = node('div'); data.tools.forEach(tool => root.append(settingsForm(tool)));
  if (!data.tools.length) root.append(node('p', 'No tools declared')); return root;
}
function deferredActions(job) {
  const controls = node('div', null, 'actions');
  const apply = (kind, extra = {}) => action('deferred', '/api/deferred', {action: kind, id: job.id, ...extra},
    kind === 'run-now' ? result => { notice('deferred', `Running task ${result.id}`); watchTask(result.id); } : null);
  controls.append(button('Run now', () => apply('run-now'), 'Confirm run now'));
  if (job.state === 'failed') controls.append(button('Retry', () => apply('retry')));
  controls.append(button('Cancel', () => apply('cancel', {confirmed: true}), 'Confirm cancel'));
  const due = node('input'); due.type = 'datetime-local'; due.value = job.due;
  due.setAttribute('aria-label', `New due time for ${job.id}`); controls.append(due);
  controls.append(button('Reschedule', () => apply('reschedule', {due: due.value.replace('T', ' ')})));
  return controls;
}
async function watchTask(id) {
  try {
    const task = await api(`/api/tasks/${id}`);
    if (task.state === taskStates.running) { setTimeout(() => watchTask(id), 2000); return; }
    notice('deferred', `${task.state}; exit ${task.exit_status ?? 'unknown'}: ${task.message}`,
      task.state === taskStates.failed);
    await refresh('deferred');
  } catch (error) { notice('deferred', error.message, true); }
}
function renderDeferred(data) {
  const root = node('div');
  root.append(node('p', `Checker: ${data.checker.agent_loaded ? 'agent loaded' : 'agent NOT loaded'}; ` +
    `${data.checker.stale ? 'STALE; ' : ''}last run ${stamp(data.checker.last_run)}`));
  root.append(node('p', data.jobs_dir, 'muted'));
  const rows = data.jobs.map(job => {
    if (!job.valid) return [job.id, 'Invalid job — inspect in Terminal', '', '', ''];
    const name = node('div'); name.append(node('strong', job.name), node('p', job.id, 'muted'));
    const description = node('div'); description.append(node('p', job.description));
    if (job.last_log) description.append(details('Last log', job.last_log, `job-${job.id}`));
    return [name, `${job.state} · ${job.attempts}/${job.max_attempts}`, stamp(job.due), description, deferredActions(job)];
  });
  root.append(rows.length ? table(['Job', 'State / attempts', 'Due', 'Description / log', 'Actions'], rows) : node('p', 'No jobs'));
  data.tasks.forEach(task => root.append(details(`${task.job}: ${task.state} (exit ${task.exit_status ?? '—'})`,
    task.message, `task-${task.id}`)));
  return root;
}
async function restartPanel(label) {
  if (label !== 'com.jacobr.mac-panel') return action('services', '/api/services/restart', {label, confirmed: true});
  notice('services', 'Restarting panel; waiting for a new instance…');
  const before = await fetch('/healthz').then(response => response.json());
  try { await api('/api/services/restart', {label, confirmed: true}); } catch (_) { /* The worker may exit before its reply. */ }
  const deadline = Date.now() + 40000;
  async function recover() {
    try {
      const response = await fetch('/healthz', {cache: 'no-store'});
      const health = await response.json();
      if (health.instance !== before.instance) { location.reload(); return; }
    } catch (_) { /* KeepAlive has not rebound the listener yet. */ }
    if (Date.now() < deadline) setTimeout(recover, 1000);
    else notice('services', 'Panel has not restarted. Refresh or inspect its LaunchAgent in Terminal.', true);
  }
  recover();
}
function renderServices(data) {
  const root = node('div');
  const rows = data.services.map(service => {
    const name = node('div', null, 'plist'); name.append(node('strong', service.label), node('p', service.kind, 'muted'));
    if (service.plist) name.append(details('Plist path', service.plist, `plist-${service.label}`));
    const logs = node('div', null, 'log');
    service.logs.forEach(log => logs.append(details(`${log.path} · ${stamp(log.modified)}`,
      log.error || log.text || '(empty)', `log-${service.label}-${log.path}`)));
    if (service.error) logs.append(node('p', service.error, 'error'));
    const status = `${service.loaded === null ? 'unknown' : service.loaded ? 'loaded' : 'not loaded'}; ${service.state}`;
    return [name, status, service.pid, service.last_exit, logs,
      service.kind === 'agent' ? button('Restart', () => restartPanel(service.label), 'Confirm restart') : 'Status only'];
  });
  root.append(table(['Service', 'State', 'PID', 'Last exit', 'Logs', 'Action'], rows)); return root;
}
function renderCrontab(data) {
  const root = node('div'); root.append(node('p', data.in_sync ? 'Tracked and live crontab are in sync' : 'Drift from macbook/crontab'));
  if (data.order_changed) root.append(node('p', 'The same lines appear in a different order.'));
  if (!data.in_sync && !data.order_changed) {
    root.append(details('Only in live', data.live_only.join('\n'), 'live-drift'));
    root.append(details('Only in tracked file', data.repo_only.join('\n'), 'repo-drift'));
  }
  const rows = data.jobs.map(job => {
    const schedule = node('div'); schedule.append(node('span', job.schedule), node('p', job.expression, 'muted'));
    const command = node('div', null, 'command'); command.title = job.command;
    command.append(node('p', job.description), details(job.command.length > 95 ? job.command.slice(0, 95) + '…' : job.command,
      job.command, `cron-${job.command}`));
    return [schedule, command, job.owner, job.log_path ? `${job.log_path}\n${stamp(job.log_modified)}` : 'None'];
  });
  root.append(table(['Schedule', 'Command / description', 'Owner', 'Last log write'], rows)); return root;
}
function renderRestore(data) {
  const root = node('div');
  root.append(node('p', `${data.enabled ? 'On' : 'Off'} · ${data.installed ? 'installed' : 'not installed'} · ` +
    `${data.up_to_date ? 'up to date' : 'needs update'}`));
  root.append(node('p', `Last snapshot: ${stamp(data.last_snapshot)}; open sessions: ${data.sessions_open ?? 'unknown'}`));
  root.append(button(data.enabled ? 'Turn off' : 'Turn on', () => action('llm-restore', '/api/llm-restore',
    {enabled: !data.enabled}, () => refresh('crontab')))); return root;
}
let guardLastResult = '';
// The verdict belongs in the status line; the full JSON stays in "Last action result".
function guardSummary(name, result) {
  const findings = Array.isArray(result.findings) ? result.findings.length : 0;
  if (name === 'audit') return `Audit: ${result.status}, ${findings} finding(s)`;
  if (name === 'inspect') return `Inspect: ${findings} finding(s) in ${result.records} records`;
  if (name === 'checkpoint') return `New known-good checkpoint: ${result.checkpoint_id}`;
  return 'Completed';
}
function renderGuard(data) {
  const root = node('div');
  const active = data.checkpoints.find(checkpoint => checkpoint.active)?.checkpoint_id ?? null;
  root.append(node('p', `Known-good checkpoint: ${active || 'none'}; saved audit: ${data.audit.status}; ` +
    `monitor: ${data.service.supervised ? 'supervised' : 'NOT supervised'}`));
  if (data.audit.checked_at) root.append(node('p', `Audit checked: ${stamp(data.audit.checked_at)}`));
  root.append(details('Saved audit', JSON.stringify(data.audit, null, 2), 'guard-audit'));
  root.append(details('Monitor status', JSON.stringify(data.service, null, 2), 'guard-service'));
  root.append(details('Checkpoints', JSON.stringify(data.checkpoints, null, 2), 'guard-checkpoints'));
  const controls = node('div', null, 'actions');
  const run = (name, extra = {}) => action('guard', '/api/guard', {action: name, ...extra}, result => {
    guardLastResult = JSON.stringify(result, null, 2);
    notice('guard', guardSummary(name, result), result.status && result.status !== 'healthy');
  });
  controls.append(button('Inspect (read-only)', () => run('inspect')), button('Audit (read-only)', () => run('audit')));
  root.append(controls);
  root.append(node('p', 'I checked the BTT menu; make the current configuration the known-good checkpoint. ' +
    (active ? `Replaces ${active}.` : 'No active checkpoint will be replaced.')));
  root.append(button('Accept current checkpoint', () => run('checkpoint', {confirmed: true, replaces: active}), 'Confirm known-good checkpoint'));
  if (guardLastResult) root.append(details('Last action result', guardLastResult, 'guard-result'));
  root.append(node('p', 'Repair is available only from the owner’s Terminal.', 'muted')); return root;
}
function renderNtfy(data) {
  const root = node('div');
  data.errors.forEach(error => root.append(node('p', error, 'error')));
  root.append(table(['Source', 'Events / matcher', 'Sender', 'Destination', 'Configuration'], data.alerts.map(alert =>
    [alert.source, `${alert.event}${alert.matcher ? ' · ' + alert.matcher : ''}`, alert.script || '—', alert.destination, alert.configuration])));
  root.append(button('Send BTT guard test alert', () => action('ntfy', '/api/ntfy/test', {confirmed: true}, result =>
    notice('ntfy', result.sent ? 'Test alert sent' : 'Test alert was not sent', !result.sent)), 'Confirm phone alert'));
  return root;
}
const renderers = {settings: renderSettings, deferred: renderDeferred, services: renderServices,
  crontab: renderCrontab, 'llm-restore': renderRestore, guard: renderGuard, ntfy: renderNtfy};
async function refreshAll() {
  sectionNames.forEach(section => notice(section, ''));
  await Promise.all(sectionNames.map(refresh));
}
function poll(section) {
  const target = document.getElementById(section);
  if (target.contains(document.activeElement) || target.querySelector('button.armed')) return;
  refresh(section);
}
document.querySelector('#refresh').addEventListener('click', refreshAll);
refreshAll();
setInterval(() => { poll('services'); poll('deferred'); }, 15000);
