// BTT guard: read-only inspection, audits and the explicit known-good checkpoint; repair stays in Terminal.
import {button, details, keyValues, node, notice, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {Level, status, worst} from './status_cell.mjs';

const SECTION = 'guard';
const AUDIT_LEVELS = {healthy: Level.OK, drift: Level.ERROR, unavailable: Level.WARN, unreadable: Level.WARN, none: Level.WARN};
// The last inspect/audit/checkpoint result outlives polls so its findings stay readable.
let lastResult = null;

function monitorStatus(service) {
  const heartbeat = service.heartbeat || {};
  const detail = [`heartbeat ${heartbeat.state ?? 'unknown'}${heartbeat.fresh === false ? ' (not fresh)' : ''}`,
    service.interval_seconds ? `every ${service.interval_seconds} s` : null].filter(Boolean).join(' · ');
  // The scheduled monitor is optional; without it the panel's own audit is the check, so this is not an error.
  if (!service.installed) return {level: Level.IDLE, node: status(Level.IDLE, 'monitor not installed (optional)', 'no scheduled alerts')};
  if (!service.supervised) return {level: Level.ERROR, node: status(Level.ERROR, 'monitor NOT supervised', detail)};
  return {level: Level.OK, node: status(Level.OK, 'monitor supervised', detail)};
}

function auditStatus(audit) {
  const level = AUDIT_LEVELS[audit.status] ?? Level.UNKNOWN;
  const findings = Array.isArray(audit.findings) ? audit.findings.length : 0;
  const detail = node('span');
  detail.append(`${findings} finding${findings === 1 ? '' : 's'}`);
  if (audit.checked_at) detail.append(' · checked ', timeNode(audit.checked_at));
  if (audit.btt_version) detail.append(` · BTT ${audit.btt_version}`);
  return {level, node: status(level, `audit ${audit.status}`, detail)};
}

function findingsList(findings) {
  const list = node('ul', null, 'findings');
  (findings || []).forEach(finding => list.append(node('li', `${finding.kind}: ${finding.detail}${finding.uuid ? ` (${finding.uuid})` : ''}`)));
  return list;
}

function resultSummary(name, result) {
  const findings = Array.isArray(result.findings) ? result.findings.length : 0;
  if (name === 'audit') return `Audit: ${result.status}, ${findings} finding(s)`;
  if (name === 'inspect') return `Inspect: ${findings} finding(s) in ${result.records} records`;
  if (name === 'checkpoint') return `New known-good checkpoint: ${result.checkpoint_id}`;
  return 'Completed';
}

function runGuard(name, extra = {}) {
  return action(SECTION, '/api/guard', {action: name, ...extra}, {after: result => {
    lastResult = {name, result, at: Date.now() / 1000};
    const level = result.status && result.status !== 'healthy' ? 'error' : 'ok';
    notice(SECTION, resultSummary(name, result), level);
  }});
}

function lastResultBlock() {
  const title = node('span');
  title.append(`Last action: ${resultSummary(lastResult.name, lastResult.result)} · `, timeNode(lastResult.at));
  const body = node('div');
  if (Array.isArray(lastResult.result.findings)) body.append(findingsList(lastResult.result.findings));
  body.append(details('Raw result', JSON.stringify(lastResult.result, null, 2), 'guard-result-raw'));
  const block = details(title, body, 'guard-result');
  block.open = true;
  return block;
}

function actionsBlock(active) {
  const root = node('div');
  const controls = node('div', null, 'actions');
  controls.append(button('Inspect (read-only)', () => runGuard('inspect'), {key: 'inspect'}),
    button('Audit (read-only)', () => runGuard('audit'), {key: 'audit'}));
  controls.append(button('Accept current configuration as known-good', () => runGuard('checkpoint', {confirmed: true, replaces: active}),
    {confirm: active ? `Confirm: replace ${active}` : 'Confirm: create the first checkpoint', key: 'checkpoint'}));
  root.append(controls);
  root.append(node('p', 'Accept only after checking the BTT menu yourself. Repair is available only from the owner’s Terminal.', 'muted small'));
  return root;
}

export function render(data) {
  const root = node('div');
  const active = data.checkpoints.find(checkpoint => checkpoint.active)?.checkpoint_id ?? null;
  const monitor = monitorStatus(data.service);
  const audit = auditStatus(data.audit);
  const checkpoint = active ? `${active} (${data.checkpoints.length} stored)` : `none (${data.checkpoints.length} stored)`;
  root.append(keyValues([['Monitor', monitor.node], ['Audit', audit.node],
    ['Known-good checkpoint', status(active ? Level.OK : Level.WARN, checkpoint)]]));
  if (Array.isArray(data.audit.findings) && data.audit.findings.length) root.append(findingsList(data.audit.findings));
  root.append(details('Raw status', JSON.stringify({audit: data.audit, monitor: data.service, checkpoints: data.checkpoints}, null, 2), 'guard-raw'));
  root.append(actionsBlock(active));
  if (lastResult) root.append(lastResultBlock());
  const summary = `${monitor.node.querySelector('.text').textContent} · audit ${data.audit.status}`;
  return {node: root, summary, level: worst([monitor.level, audit.level, active ? Level.OK : Level.WARN])};
}
