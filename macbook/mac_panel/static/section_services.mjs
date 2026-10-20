// Background services: launchd inventory with log tails fetched only for the entries the reader opens.
import {api, health} from './panel_api.mjs';
import {button, details, node, notice, table, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {Level, status, tag, worst} from './status_cell.mjs';

const SECTION = 'services';
export const PANEL_LABEL = 'com.jacobr.mac-panel';
const SIGNALS = {1: 'SIGHUP', 2: 'SIGINT', 9: 'SIGKILL', 15: 'SIGTERM'};
const RESTART_WAIT_MS = 40000;

function exitWords(code) {
  if (code === null || code === undefined) return null;
  if (code < 0) return `last exit −${-code}${SIGNALS[-code] ? ` (${SIGNALS[-code]})` : ''}`;
  return `last exit ${code}`;
}

// launchd lists the job as disabled, as the local-service `stop` verb leaves it: off on purpose.
const isDisabled = service => service.loaded === false && service.disabled;

export function serviceState(service) {
  if (service.loaded === null) return {level: Level.UNKNOWN, text: 'unknown', detail: service.error || 'launchd gave no status'};
  if (isDisabled(service)) return {level: Level.IDLE, text: 'disabled', detail: 'launchd will not start it until it is enabled'};
  if (service.loaded === false) return {level: Level.WARN, text: 'not loaded', detail: exitWords(service.last_exit)};
  if (service.pid) return {level: Level.OK, text: `running · pid ${service.pid}`, detail: exitWords(service.last_exit)};
  const failed = service.last_exit !== null && service.last_exit !== 0;
  const text = service.kind === 'agent' ? 'idle' : service.state;
  return failed ? {level: Level.WARN, text: `${text} · ${exitWords(service.last_exit)}`, detail: null}
    : {level: Level.IDLE, text, detail: exitWords(service.last_exit)};
}

export async function restartPanel(label) {
  if (label !== PANEL_LABEL) return action(SECTION, '/api/services/restart', {label, confirmed: true});
  notice(SECTION, 'Restarting the panel; waiting for the new instance…', 'working');
  const before = await health().catch(() => null);
  try { await api('/api/services/restart', {label, confirmed: true}); } catch (_) { /* The worker may exit before its reply. */ }
  const deadline = Date.now() + RESTART_WAIT_MS;
  async function recover() {
    try {
      const current = await health();
      if (!before || current.instance !== before.instance) {
        location.reload();
        return;
      }
    } catch (_) { /* KeepAlive has not rebound the listener yet. */ }
    if (Date.now() < deadline) setTimeout(recover, 1000);
    else notice(SECTION, 'Panel has not restarted. Refresh or inspect its LaunchAgent in Terminal.', 'error');
  }
  recover();
  return undefined;
}

// One request per service fills every open tail in its row; polls re-open the same keys and re-fetch.
function tailLoader(label, cell) {
  let pending = null;
  return () => {
    pending ??= api(`/api/services/logs/${encodeURIComponent(label)}`).then(payload => {
      payload.logs.forEach(log => {
        const pre = cell.querySelector(`pre[data-path="${CSS.escape(log.path)}"]`);
        if (pre) pre.textContent = log.error || log.text || '(empty)';
      });
    }).catch(error => {
      cell.querySelectorAll('pre').forEach(pre => { pre.textContent = error.message; });
    });
    return pending;
  };
}

function logEntry(service, log, load) {
  const title = node('span');
  title.append(log.stream, ' · ', log.error ? node('span', log.error, 'muted') : timeNode(log.modified, 'never written'));
  const pre = node('pre', 'Loading…');
  pre.dataset.path = log.path;
  const entry = details(title, pre, `log-${service.label}-${log.path}`);
  entry.title = log.path;
  entry.addEventListener('toggle', () => { if (entry.open) load(); });
  return entry;
}

function logsCell(service, state) {
  const cell = node('div');
  const load = tailLoader(service.label, cell);
  service.logs.forEach(log => cell.append(logEntry(service, log, load)));
  if (!service.logs.length) cell.append(node('span', 'no log declared', 'muted'));
  if (service.error && state.level !== Level.UNKNOWN) cell.append(node('p', service.error, 'error'));
  return cell;
}

function serviceRow(service) {
  const state = serviceState(service);
  const name = node('div');
  name.append(node('strong', service.label), ' ', tag(service.kind));
  if (service.plist) name.append(node('div', service.plist, 'muted small'));
  const act = service.kind === 'agent'
    ? button('Restart', () => restartPanel(service.label), {confirm: 'Confirm restart', key: `restart-${service.label}`})
    : node('span', 'status only', 'muted');
  return {state, cells: [name, status(state.level, state.text, state.detail), logsCell(service, state), act]};
}

export function render(data) {
  const counts = {running: 0, idle: 0, disabled: 0, attention: 0};
  const levels = [];
  const rows = data.services.map(service => {
    const {state, cells} = serviceRow(service);
    levels.push(state.level);
    if (state.level === Level.OK) counts.running += 1;
    else if (isDisabled(service)) counts.disabled += 1;
    else if (state.level === Level.IDLE) counts.idle += 1;
    else counts.attention += 1;
    return cells;
  });
  const root = node('div');
  const attention = counts.attention ? `${counts.attention} ${counts.attention === 1 ? 'needs' : 'need'} attention` : '';
  const disabled = counts.disabled ? `${counts.disabled} disabled` : '';
  const line = [`${counts.running} running`, `${counts.idle} idle`, disabled, attention].filter(Boolean).join(' · ');
  root.append(node('p', line, 'summary-line'), table(['Service', 'State', 'Logs', 'Action'], rows));
  // The tab tooltip has room for the count that matters, not the whole breakdown.
  const summary = [`${counts.running} running`, attention].filter(Boolean).join(' · ');
  // Anything needing attention marks the tab at least yellow, even a service whose state is unknown;
  // idle and disabled services are normal, so otherwise the tab is green.
  return {node: root, summary, level: counts.attention ? worst([...levels, Level.WARN]) : Level.OK};
}
