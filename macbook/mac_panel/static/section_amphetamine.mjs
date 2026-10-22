// Amphetamine owns the values; edits are revision checked by its controller.
import {button, isChanged, keyValues, node} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {Level, status} from './status_cell.mjs';

const SECTION = 'amphetamine';

function setting(label, input) {
  const row = node('div', null, 'setting');
  const text = node('label', label);
  text.htmlFor = input.id;
  row.append(text, input);
  return row;
}

function controls(data) {
  const form = node('form', null, 'tool');
  const enabled = node('input');
  enabled.type = 'checkbox';
  enabled.id = enabled.dataset.key = 'amphetamine-enabled';
  enabled.checked = enabled.defaultChecked = data.enabled;
  const minutes = node('input');
  minutes.type = 'number';
  minutes.id = minutes.dataset.key = 'amphetamine-minutes';
  minutes.min = data.minimum_minutes;
  minutes.max = data.maximum_minutes;
  minutes.step = 1;
  minutes.required = true;
  minutes.value = minutes.defaultValue = String(data.delay_seconds / 60);
  form.append(setting('Automatic screen lock', enabled), setting('Idle time (minutes)', minutes));
  const actions = node('div', null, 'actions');
  const apply = node('button', 'Apply');
  apply.type = 'submit';
  apply.title = 'Restarts Amphetamine, preserves an indefinite session, and starts one when enabling automatic lock.';
  const reload = button('Reload values', () => refresh(SECTION));
  reload.hidden = true;
  actions.append(apply, reload);
  form.append(actions);
  const update = () => { apply.disabled = Boolean(data.error) || ![enabled, minutes].some(isChanged); };
  form.addEventListener('input', update);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    apply.disabled = true;
    await action(SECTION, '/api/amphetamine', {
      enabled: enabled.checked, minutes: Number(minutes.value), revision: data.revision,
    }, {reloadOnError: false, onError: error => { reload.hidden = error.status !== 409; update(); }});
  });
  update();
  return form;
}

export function render(data) {
  const root = node('div');
  const active = data.session?.active;
  const summary = data.enabled ? `Lock after ${data.delay_seconds / 60} min` : 'Automatic lock off';
  const level = data.error ? Level.ERROR : data.enabled && !active ? Level.WARN : data.enabled ? Level.OK : Level.IDLE;
  root.append(status(level, summary), controls(data));
  root.append(keyValues([
    ['Keep-awake session', active ? 'Active' : data.session ? 'Inactive' : 'Unavailable'],
    ['Display after locking', data.display_sleep_locked ? 'Allowed to sleep' : 'Kept on'],
    ['Battery cutoff', data.low_battery_enabled ? `Below ${data.low_battery_percent}%` : 'Off'],
  ]));
  if (data.error) root.append(node('p', data.error, 'error'));
  if (data.enabled && !active) root.append(node('p', 'Automatic locking needs an active Amphetamine session.', 'warning'));
  return {node: root, summary, level};
}
