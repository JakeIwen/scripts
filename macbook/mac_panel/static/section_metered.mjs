// Host-local controls; remote consumers are telemetry, never editable here.
import {button, details, node, table, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {Level, status} from './status_cell.mjs';

const SECTION = 'metered';
const send = body => action(SECTION, '/api/metered', body);

function info(text, key) {
  const content = node('p', text);
  const result = details('Info', content, key);
  result.title = text;
  return result;
}

function select(options, label, value = '') {
  const result = node('select');
  result.setAttribute('aria-label', label);
  result.dataset.key = label;
  options.forEach(([key, text]) => {
    const option = node('option', text);
    option.value = key;
    option.selected = option.defaultSelected = key === value;
    result.append(option);
  });
  return result;
}

function ssids(data) {
  const root = node('div', null, 'tool');
  root.append(node('h3', 'Metered SSIDs'));
  const chips = node('div', null, 'actions');
  const pending = new Set(data.pending.map(op => op.ssid));
  data.config.metered_ssids.forEach(ssid => {
    const chip = node('div', null, 'actions');
    chip.append(node('span', ssid));
    if (pending.has(ssid)) chip.append(node('span', 'pending sync', 'muted small'));
    chip.append(button('Remove', () => send({action: 'ssid-remove', ssid, revision: data.config.revision}),
      {confirm: 'Confirm remove', key: `remove-${ssid}`, disabled: pending.has(ssid)}));
    chips.append(chip);
  });
  const form = node('form', null, 'actions');
  const input = node('input');
  input.required = true;
  input.maxLength = 32;
  input.placeholder = 'Exact SSID';
  input.setAttribute('aria-label', 'SSID to meter');
  input.dataset.key = 'metered-ssid';
  const add = node('button', 'Add');
  add.type = 'submit';
  form.append(input, add, button('Sync now', () => send({action: 'sync'})));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.reportValidity()) await send({action: 'ssid-add', ssid: input.value});
  });
  root.append(chips, form, info('Exact, case-sensitive SSIDs. Additions work offline; removal requires the Pi and its current revision.', 'ssid-info'));
  if (data.sync.error) root.append(details('Sync error', node('p', data.sync.error), 'sync-error'));
  return root;
}

function consumerStatus(row, state, fresh) {
  if (row.how === 'unprotected') return 'Unprotected';
  if (row.how === 'advisory') return 'Advisory only';
  if (!fresh || !state) return 'Unknown / stale';
  if (!state.permitted) return state.actual === 'held' ? 'Held' : 'Denied; enforcement unverified';
  if (state.actual === 'held') return 'Permitted; flag still set';
  if (state.actual === 'stopped_manually') return 'Permitted; native hold';
  return state.approval ? 'Temporarily permitted' : 'Permitted';
}

function approvals(row, data) {
  const controls = node('div', null, 'actions');
  const choice = select([['', 'Approve…'], ['1h', '1 hour'], ['4h', '4 hours'], ['episode', 'This episode']],
    `Approve ${row.label}`);
  choice.addEventListener('change', async () => {
    if (choice.value) await send({action: 'approve', consumer: row.id,
      duration: choice.value, revision: data.policy.revision});
  });
  const revoke = button('Revoke', () => send({action: 'revoke', consumer: row.id, revision: data.policy.revision}),
    {disabled: !data.policy.approvals[row.id], key: `revoke-${row.id}`});
  controls.append(choice, revoke);
  return controls;
}

function consumers(data, host) {
  const root = node('div', null, 'tool');
  const local = host === 'mac';
  const state = local ? data.state : data.pi_state;
  const fresh = local ? data.fresh : state && state.evaluated_at <= Date.now() / 1000 && state.valid_until > Date.now() / 1000;
  root.append(node('h3', local ? 'Mac consumers' : 'Pi consumers'));
  if (!local) {
    const age = node('p', 'Last Pi evaluation: ', 'muted');
    age.append(timeNode(state?.evaluated_at || null));
    root.append(age);
  }
  const rows = data.registry.filter(row => row.host === host).map(row => {
    const current = state?.consumers[row.id];
    const label = node('div');
    label.append(node('strong', row.label), info([row.note, current?.reason].filter(Boolean).join(' · '), `info-${row.id}`));
    const display = node('div', consumerStatus(row, current, fresh));
    if (fresh && current?.approval?.until) display.append(' until ', timeNode(current.approval.until));
    return local ? [label, display, approvals(row, data)] : [label, display];
  });
  root.append(table(local ? ['Consumer', 'State', ''] : ['Consumer', 'State'], rows));
  return root;
}

function advanced(data) {
  const root = node('div', null, 'tool');
  const mode = select([['auto', 'Auto'], ['metered', 'Metered'], ['unmetered', 'Unmetered']],
    'Mac mode', data.policy.mode.value);
  const duration = select([['1h', '1 hour'], ['4h', '4 hours']], 'Mode duration', '1h');
  const actions = node('div', null, 'actions');
  actions.append(mode, duration, button('Set mode', () => send({action: 'mode', value: mode.value,
    duration: mode.value === 'auto' ? '' : duration.value, revision: data.policy.revision})));
  root.append(actions);
  data.registry.filter(row => row.host === 'mac').forEach(row => {
    const label = node('label', null, 'setting');
    const input = node('input');
    input.type = 'checkbox';
    input.checked = input.defaultChecked = data.policy.exempt.includes(row.id);
    input.dataset.key = `exempt-${row.id}`;
    input.addEventListener('change', () => send({action: 'exempt', consumer: row.id,
      enabled: input.checked ? 'on' : 'off', revision: data.policy.revision}));
    label.append(`Do not gate ${row.label}`, input);
    root.append(label);
  });
  return details('Advanced', root, 'metered-advanced');
}

function lowData(data) {
  const root = node('div', null, 'tool');
  const enabled = data.low_data_mode.enabled;
  const toggle = button(`Low Data Mode control: ${enabled ? 'On' : 'Off'}`, () => send({
    action: 'low-data-mode', enabled: !enabled, revision: data.low_data_mode.revision, confirmed: true,
  }), {confirm: enabled ? 'Confirm disable' : 'Time Machine test passed: enable', key: 'ldm-enable'});
  toggle.setAttribute('role', 'switch');
  toggle.setAttribute('aria-checked', String(enabled));
  root.append(toggle, info('Enable only after the sudoers / LAN Time Machine test passes. Wi-Fi is constrained while metered and cleared when unmetered or approved. A flag this control did not set (native per-network Low Data Mode) is left alone.', 'ldm-info'));
  if (data.low_data_mode.error) root.append(node('p', data.low_data_mode.error, 'error'));
  return root;
}

export function render(data) {
  const root = node('div');
  const verdict = data.fresh ? data.state.verdict.metered : null;
  const summary = verdict === null ? 'Unknown' : verdict ? 'Metered' : 'Unmetered';
  const level = verdict === false ? Level.OK : Level.WARN;
  root.append(status(level, summary), node('p', data.fresh ? data.state.verdict.reason : 'State is missing or stale'));
  if (data.state.effective.source === 'mode') root.append(node('p', `Mode override: ${data.policy.mode.value}`, 'muted'));
  root.append(ssids(data), lowData(data), consumers(data, 'mac'), advanced(data), consumers(data, 'pi'));
  return {node: root, summary, level};
}
