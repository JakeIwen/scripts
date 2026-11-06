// Background services: launchd inventory with log tails fetched only for the entries the reader opens.
import {api, health} from './panel_api.mjs';
import {button, card, confirmText, details, emptyState, info, node, notice, tag, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {fillSlots, historySlot, historyToggle} from './section_services_history.mjs';
import {Level, status, worst} from './status_cell.mjs';

const SECTION = 'services';
const COPY = JOBS_COPY.services;
const VERBS = ['restart', 'start', 'stop'];
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


export const serviceAnchor = label => `service-${label}`;

// A verb that cannot do anything in the service's current state stays visible but disabled, with the reason.
function blockedReason(service, verb) {
  if (verb === 'start' && service.pid) return COPY.blocked.start;
  if (verb !== 'start' && service.loaded === false) return COPY.blocked[verb];
  return null;
}

function verbButton(service, verb) {
  const reason = blockedReason(service, verb);
  const run = () => (service.label === PANEL_LABEL ? restartPanel(service.label)
    : action(SECTION, '/api/services', {action: verb, label: service.label, confirmed: true}));
  return button(COPY.verbs[verb], run, {
    confirm: confirmText(`${verb} ${service.label.replace('com.jacobr.', '')}`), key: `${verb}-${service.label}`,
    disabled: Boolean(reason), title: reason ?? COPY.verbHelp[verb],
  });
}

function sudoNotice(command) {
  const block = node('div', null, 'sudo-install');
  block.append(node('span', COPY.sudoInstall, 'muted small'), ' ', node('code', command, 'copyable'));
  return block;
}

function controls(service, installCommand) {
  const row = node('div', null, 'actions');
  if (service.kind === 'daemon' && service.sudo_rule === 'missing') {
    row.append(button(COPY.verbs.restart, () => undefined, {disabled: true, key: `restart-${service.label}`, title: COPY.sudoMissing}),
      info(COPY.sudoMissing));
    return [row, sudoNotice(installCommand)];
  }
  VERBS.filter(verb => service.actions.includes(verb)).forEach(verb => row.append(verbButton(service, verb)));
  if (!service.actions.length) row.append(node('span', COPY.statusOnly, 'muted'));
  return [row];
}

function serviceCard(service, installCommand) {
  const state = serviceState(service);
  const title = node('span');
  title.append(service.label, ' ', tag(service.kind));
  const body = node('div');
  body.append(status(state.level, state.text, state.detail));
  if (service.plist) body.append(node('div', service.plist, 'muted small'));
  body.append(...controls(service, installCommand));
  body.append(historySlot(service.label), logsCell(service, state));
  return card({title, level: state.level, body, anchor: serviceAnchor(service.label)});
}

function counts(services) {
  const result = {running: 0, idle: 0, disabled: 0, attention: 0, levels: []};
  services.forEach(service => {
    const {level} = serviceState(service);
    result.levels.push(level);
    if (level === Level.OK) result.running += 1;
    else if (isDisabled(service)) result.disabled += 1;
    else if (level === Level.IDLE) result.idle += 1;
    else result.attention += 1;
  });
  return result;
}

export function render(data) {
  const count = counts(data.services);
  const root = node('div');
  const attention = count.attention ? `${count.attention} ${count.attention === 1 ? 'needs' : 'need'} attention` : '';
  const disabled = count.disabled ? `${count.disabled} disabled` : '';
  const line = [`${count.running} running`, `${count.idle} idle`, disabled, attention].filter(Boolean).join(' · ');
  const head = node('div', null, 'actions');
  head.append(node('span', line, 'summary-line'), historyToggle(() => root), info(COPY.historyInfo));
  root.append(head);
  if (!data.services.length) root.append(emptyState(COPY.empty));
  data.services.forEach(service => root.append(serviceCard(service, data.sudo_install_command)));
  fillSlots(root);
  // The tab tooltip has room for the count that matters, not the whole breakdown.
  const summary = [`${count.running} running`, attention].filter(Boolean).join(' · ');
  // Anything needing attention marks the tab at least yellow, even a service whose state is unknown;
  // idle and disabled services are normal, so otherwise the tab is green.
  return {node: root, summary, level: count.attention ? worst([...count.levels, Level.WARN]) : Level.OK};
}

export function searchItems(data) {
  return data.services.map(service => ({
    title: service.label, subtitle: serviceState(service).text, keywords: ['service', 'launchd', service.kind],
    anchor: serviceAnchor(service.label),
    actions: service.actions.map(verb => ({
      label: COPY.verbs[verb],
      run: () => stageAction(SECTION, serviceAnchor(service.label), `${verb}-${service.label}`, {arm: true}),
    })),
  }));
}

export function attentionItems(data) {
  return data.services.map(service => ({service, state: serviceState(service)}))
    .filter(({state}) => [Level.WARN, Level.ERROR, Level.UNKNOWN].includes(state.level))
    .map(({service, state}) => ({
      level: state.level === Level.ERROR ? Level.ERROR : Level.WARN,
      text: `${service.label} ${state.text}`, anchor: serviceAnchor(service.label),
    }));
}
