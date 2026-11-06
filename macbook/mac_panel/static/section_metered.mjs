// Host-local controls; remote consumers are telemetry, never editable here.
import {stateLegend, timeAxis} from './chart_axes.mjs';
import {sparkline, stateStrip} from './charts.mjs';
import {historyPanel} from './history_panel.mjs';
import {METERED_COPY as COPY} from './section_copy.mjs';
import {anchor, button, card, details, emptyState, fieldError, info, kv, node, settingRow, tag, timeNode, toggle}
  from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {goTo} from './shell.mjs';
import {Level, status} from './status_cell.mjs';

const SECTION = 'metered';
const SSID_MAX_LENGTH = 32;
const HISTORY_MAX_GAP = 600;
const send = body => action(SECTION, '/api/metered', body);
const ssidAnchor = ssid => `ssid-${ssid}`;
const consumerAnchor = id => `consumer-${id}`;

function select(options, label, value = '') {
  const result = node('select');
  result.id = result.dataset.key = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  result.setAttribute('aria-label', label);
  options.forEach(([key, text]) => {
    const option = node('option', text);
    option.value = key;
    option.selected = option.defaultSelected = key === value;
    result.append(option);
  });
  return result;
}

// ---- verdict ----

function verdictOf(data) {
  const verdict = data.fresh ? data.state.verdict.metered : null;
  if (verdict === null) return {text: 'Unknown', level: Level.WARN};
  return verdict ? {text: 'Metered', level: Level.IDLE} : {text: 'Unmetered', level: Level.OK};
}

function verdictCard(data) {
  const {text, level} = verdictOf(data);
  const body = node('div');
  body.append(status(level, text, data.fresh ? data.state.verdict.reason : COPY.stale));
  if (data.state.effective.source === 'mode') body.append(node('p', `Mode override: ${data.policy.mode.value}`, 'muted'));
  return card({title: 'Network', level, body, anchor: 'verdict'});
}

// ---- SSIDs ----

function ssidRow(data, ssid, pending) {
  const remove = button('Remove', () => send({action: 'ssid-remove', confirmed: true, ssid, revision: data.config.revision}),
    {confirm: 'Confirm: remove this SSID', key: `remove-${ssid}`, disabled: pending});
  const control = node('div', null, 'actions');
  control.append(remove);
  if (pending) control.append(tag('pending sync', Level.WARN));
  return settingRow({label: ssid, control, anchor: ssidAnchor(ssid)});
}

function addSsidForm() {
  const form = node('form', null, 'actions');
  const input = node('input');
  input.required = true;
  input.maxLength = SSID_MAX_LENGTH;
  input.placeholder = 'Exact SSID';
  input.setAttribute('aria-label', 'SSID to meter');
  input.dataset.key = 'metered-ssid';
  const add = node('button', 'Add');
  add.type = 'submit';
  form.append(input, add, button('Sync now', () => send({action: 'sync'}), {key: 'sync'}));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.reportValidity()) await send({action: 'ssid-add', ssid: input.value});
  });
  return form;
}

function ssidsCard(data) {
  const body = node('div');
  const pending = new Set(data.pending.map(op => op.ssid));
  const ssids = data.config.metered_ssids;
  if (!ssids.length) body.append(emptyState(COPY.ssidsEmpty));
  ssids.forEach(ssid => body.append(ssidRow(data, ssid, pending.has(ssid))));
  body.append(anchor(addSsidForm(), 'ssid-add'));
  if (data.sync.error) body.append(details('Sync error', fieldError(data.sync.error), 'sync-error'));
  const title = node('span', COPY.ssidsTitle);
  title.append(info(COPY.ssidsInfo));
  return card({title, body, anchor: 'ssids'});
}

// ---- Low Data Mode ----

function lowDataCard(data) {
  const enabled = data.low_data_mode.enabled;
  const control = toggle({
    label: enabled ? 'On' : 'Off', on: enabled, key: 'ldm-enable',
    confirm: enabled ? 'turn Low Data Mode off' : 'turn Low Data Mode on',
    onChange: next => send({action: 'low-data-mode', enabled: next, revision: data.low_data_mode.revision, confirmed: true}),
  });
  control.id = 'ldm-switch';
  const body = node('div');
  body.append(settingRow({label: 'Low Data Mode control', info: COPY.lowDataInfo, control, anchor: 'low-data-mode'}));
  if (data.low_data_mode.error) body.append(fieldError(data.low_data_mode.error));
  return card({title: COPY.lowDataTitle, body});
}

// ---- consumers ----

function consumerState(row, state, fresh) {
  if (row.how === 'unprotected') return {text: 'Unprotected', level: Level.WARN};
  if (row.how === 'advisory') return {text: 'Advisory only', level: Level.IDLE};
  if (!fresh || !state) return {text: 'Unknown / stale', level: Level.UNKNOWN};
  if (!state.permitted) {
    return state.actual === 'held' ? {text: 'Held', level: Level.IDLE} : {text: 'Denied; enforcement unverified', level: Level.WARN};
  }
  if (state.actual === 'held') return {text: 'Permitted; flag still set', level: Level.WARN};
  if (state.actual === 'stopped_manually') return {text: 'Permitted; native hold', level: Level.IDLE};
  return {text: state.approval ? 'Temporarily permitted' : 'Permitted', level: Level.OK};
}

function approvals(row, data) {
  const controls = node('span', null, 'actions');
  const choice = select([['', 'Approve…'], ['1h', '1 hour'], ['4h', '4 hours'], ['episode', 'This episode']],
    `Approve ${row.label}`);
  choice.addEventListener('change', async () => {
    if (choice.value) await send({action: 'approve', consumer: row.id, duration: choice.value, revision: data.policy.revision});
  });
  const revoke = button('Revoke', () => send({action: 'revoke', consumer: row.id, revision: data.policy.revision}),
    {disabled: !data.policy.approvals[row.id], key: `revoke-${row.id}`});
  controls.append(choice, revoke);
  return controls;
}

function consumerRow(row, data, host, state, fresh) {
  const current = state?.consumers[row.id];
  const shown = consumerState(row, current, fresh);
  const control = node('div', null, 'actions');
  const label = tag(shown.text, shown.level);
  control.append(label);
  if (fresh && current?.approval?.until) control.append('until ', timeNode(current.approval.until));
  if (host === 'mac') control.append(approvals(row, data));
  const help = [row.note, current?.reason].filter(Boolean).join(' · ');
  return settingRow({label: row.label, info: help || undefined, control, anchor: consumerAnchor(row.id)});
}

function consumersCard(data, host) {
  const local = host === 'mac';
  const state = local ? data.state : data.pi_state;
  const now = Date.now() / 1000;
  const fresh = local ? data.fresh : Boolean(state) && state.evaluated_at <= now && state.valid_until > now;
  const body = node('div');
  if (!local) {
    const age = node('p', 'Last Pi evaluation: ', 'muted');
    age.append(timeNode(state?.evaluated_at || null));
    body.append(age);
  }
  data.registry.filter(row => row.host === host).forEach(row => body.append(consumerRow(row, data, host, state, fresh)));
  const title = node('span', local ? COPY.macConsumersTitle : COPY.piConsumersTitle);
  title.append(info(local ? COPY.approveInfo : COPY.piConsumersInfo));
  return card({title, body});
}

// ---- advanced ----

function advancedCard(data) {
  const body = node('div');
  const mode = select([['auto', 'Auto'], ['metered', 'Metered'], ['unmetered', 'Unmetered']], 'Mac mode', data.policy.mode.value);
  const duration = select([['1h', '1 hour'], ['4h', '4 hours']], 'Mode duration', '1h');
  const control = node('div', null, 'actions');
  control.append(mode, duration, button('Set mode', () => send({action: 'mode', value: mode.value,
    duration: mode.value === 'auto' ? '' : duration.value, revision: data.policy.revision}), {key: 'set-mode'}));
  body.append(settingRow({label: COPY.modeTitle, info: COPY.modeInfo, control, anchor: 'mode'}));
  data.registry.filter(row => row.host === 'mac').forEach(row => {
    const on = data.policy.exempt.includes(row.id);
    const exempt = toggle({label: on ? 'Exempt' : 'Gated', on, key: `exempt-${row.id}`,
      onChange: next => send({action: 'exempt', consumer: row.id, enabled: next ? 'on' : 'off', revision: data.policy.revision})});
    body.append(settingRow({label: `Do not gate ${row.label}`, info: COPY.exemptInfo, control: exempt}));
  });
  return details(COPY.advancedTitle, body, 'metered-advanced');
}

// ---- history ----

const toBands = rows => rows.map(({ts, level, text}) => ({ts, level, text: text ?? level}));

const strip = (rows, window) => stateStrip({samples: toBands(rows), ...window, maxGap: HISTORY_MAX_GAP, legend: false});

// A group of strips shares one time axis and one key to the band colours.
const groupFooter = window => [timeAxis(window), stateLegend()];

function drawHistory(data, series, window) {
  const charts = [];
  const verdict = series.verdict ?? [];
  if (verdict.length) charts.push(node('h4', 'Metered or not'), strip(verdict, window));
  const lowData = series.low_data_mode ?? [];
  if (lowData.some(point => point.value !== null)) {
    charts.push(node('h4', 'Low Data Mode'), sparkline({
      points: lowData.filter(point => point.value !== null), ...window, min: 0, max: 1, height: 24,
      format: value => (value ? 'on' : 'off'), maxGap: HISTORY_MAX_GAP,
    }));
  }
  if (charts.length) charts.push(...groupFooter(window));
  const labels = new Map(data.registry.map(row => [row.id, row.label]));
  const perConsumer = node('div');
  Object.entries(series).filter(([key]) => key.startsWith('consumer:')).forEach(([key, rows]) => {
    perConsumer.append(node('h4', labels.get(key.slice('consumer:'.length)) ?? key.slice('consumer:'.length)), strip(rows, window));
  });
  if (perConsumer.childElementCount) {
    perConsumer.append(...groupFooter(window));
    charts.push(details(COPY.consumerHistoryTitle, perConsumer, 'consumer-history'));
  }
  return charts;
}

function historyCard(data) {
  const title = node('span', COPY.historyTitle);
  title.append(info(COPY.historyInfo));
  const body = historyPanel({id: SECTION, section: 'metered', emptyText: COPY.noHistory,
    draw: (series, window) => drawHistory(data, series, window)});
  return card({title, body, anchor: 'history'});
}

// ---- section contract ----

export function render(data) {
  const root = node('div');
  const {text, level} = verdictOf(data);
  root.append(verdictCard(data), historyCard(data), ssidsCard(data), lowDataCard(data),
    consumersCard(data, 'mac'), advancedCard(data), consumersCard(data, 'pi'));
  return {node: root, summary: text, level};
}

export function attentionItems(data) {
  const items = [];
  if (!data.fresh) items.push({level: Level.WARN, text: COPY.stale, anchor: 'verdict'});
  if (data.pending.length) {
    items.push({level: Level.WARN, text: `${data.pending.length} SSID change${data.pending.length === 1 ? '' : 's'} waiting to sync`, anchor: 'ssids'});
  }
  if (data.sync.error) items.push({level: Level.WARN, text: `Sync failed: ${data.sync.error}`, anchor: 'ssids'});
  if (data.low_data_mode.error) items.push({level: Level.WARN, text: data.low_data_mode.error, anchor: 'low-data-mode'});
  return items;
}

const jump = (anchorId, label) => ({label, run: () => goTo(SECTION, anchorId, {focus: true})});

export function searchItems(data) {
  const items = [
    {title: 'Low Data Mode', subtitle: 'Metered', keywords: ['constrained', 'wifi'], anchor: 'low-data-mode', actions: [jump('low-data-mode', 'Toggle')]},
    {title: 'Sync metered SSIDs now', subtitle: 'Metered', keywords: ['pi', 'revision'], anchor: 'ssid-add', actions: [jump('ssid-add', 'Open')]},
    {title: 'Add a metered SSID', subtitle: 'Metered', anchor: 'ssid-add', actions: [jump('ssid-add', 'Open')]},
    {title: 'Mac metering mode', subtitle: 'Metered', keywords: ['override', 'auto'], anchor: 'mode', actions: [jump('mode', 'Open')]},
  ];
  data.config.metered_ssids.forEach(ssid => items.push({
    title: ssid, subtitle: 'Metered SSID', keywords: ['wifi', 'network'], anchor: ssidAnchor(ssid),
    actions: [jump(ssidAnchor(ssid), 'Remove')],
  }));
  data.registry.forEach(row => items.push({
    title: row.label, subtitle: `Metered consumer on ${row.host === 'mac' ? 'the Mac' : 'the Pi'}`,
    keywords: [row.id, row.host, 'approve', 'revoke'], anchor: consumerAnchor(row.id),
    actions: row.host === 'mac' ? [jump(consumerAnchor(row.id), 'Approve')] : [],
  }));
  return items;
}
