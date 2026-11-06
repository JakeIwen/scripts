// Amphetamine owns the values; edits are revision checked by its controller.
import {button, card, fieldError, info, isChanged, kv, node, settingRow, tag, toggle} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {AMPHETAMINE_COPY as COPY} from './section_copy.mjs';
import {goTo} from './shell.mjs';
import {Level, status} from './status_cell.mjs';

const SECTION = 'amphetamine';
const CONFLICT = 409;
const LOCK_ANCHOR = 'auto-lock';
const MINUTES_ANCHOR = 'idle-minutes';

function levelOf(data) {
  const active = data.session?.active;
  if (data.error) return Level.ERROR;
  if (data.enabled && !active) return Level.WARN;
  return data.enabled ? Level.OK : Level.IDLE;
}

// One save path for both controls: the switch keeps the saved minutes, the Apply button keeps the saved switch.
function lockControls(data) {
  const reload = button('Reload values', () => refresh(SECTION), {key: 'reload'});
  reload.hidden = true;
  const save = (enabled, minutes) => action(SECTION, '/api/amphetamine', {enabled, minutes, revision: data.revision}, {
    reloadOnError: false, onError: error => { reload.hidden = error.status !== CONFLICT; },
  });
  const savedMinutes = data.delay_seconds / 60;
  const lock = toggle({label: data.enabled ? 'On' : 'Off', on: data.enabled, key: 'amphetamine-enabled',
    disabled: Boolean(data.error), onChange: next => save(next, savedMinutes)});
  lock.id = 'amphetamine-enabled';
  const minutes = node('input');
  minutes.type = 'number';
  minutes.id = minutes.dataset.key = 'amphetamine-minutes';
  minutes.min = data.minimum_minutes;
  minutes.max = data.maximum_minutes;
  minutes.step = 1;
  minutes.required = true;
  minutes.value = minutes.defaultValue = String(savedMinutes);
  const apply = node('button', 'Apply');
  apply.type = 'submit';
  const form = node('form', null, 'actions');
  form.append(minutes, apply, reload);
  const update = () => { apply.disabled = Boolean(data.error) || !isChanged(minutes); };
  form.addEventListener('input', update);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    apply.disabled = true;
    await save(data.enabled, Number(minutes.value));
    update();
  });
  update();
  return {lock, form};
}

function sessionText(session) {
  if (!session) return tag('Unavailable', Level.UNKNOWN);
  return session.active ? tag('Active', Level.OK) : tag('Inactive', Level.IDLE);
}

function withInfo(content, help) {
  const result = node('span');
  result.append(content instanceof Node ? content : document.createTextNode(content), info(help));
  return result;
}

export function render(data) {
  const root = node('div');
  const summary = data.enabled ? `Lock after ${data.delay_seconds / 60} min` : 'Automatic lock off';
  const level = levelOf(data);
  const {lock, form} = lockControls(data);
  const body = node('div');
  body.append(status(level, summary),
    settingRow({label: COPY.lockTitle, info: COPY.lockInfo, control: lock, anchor: LOCK_ANCHOR}),
    settingRow({label: COPY.minutesLabel, info: COPY.minutesInfo, control: form, unit: 'minutes', anchor: MINUTES_ANCHOR}));
  if (data.error) body.append(fieldError(data.error));
  if (data.enabled && !data.session?.active) body.append(node('p', COPY.needsSession, 'warning'));
  root.append(card({title: COPY.lockTitle, level, body}));

  root.append(card({title: 'Session', body: kv([
    [COPY.sessionLabel, withInfo(sessionText(data.session), COPY.sessionInfo)],
    [COPY.displayLabel, withInfo(data.display_sleep_locked ? 'Allowed to sleep' : 'Kept on', COPY.displayInfo)],
    [COPY.batteryLabel, withInfo(data.low_battery_enabled ? `Below ${data.low_battery_percent}%` : 'Off', COPY.batteryInfo)],
  ])}));
  return {node: root, summary, level};
}

export function attentionItems(data) {
  if (data.error) return [{level: Level.ERROR, text: data.error, anchor: LOCK_ANCHOR}];
  if (data.enabled && !data.session?.active) return [{level: Level.WARN, text: COPY.needsSession, anchor: LOCK_ANCHOR}];
  return [];
}

const jump = (anchorId, label) => ({label, run: () => goTo(SECTION, anchorId, {focus: true})});

export function searchItems() {
  return [
    {title: COPY.lockTitle, subtitle: 'Amphetamine', keywords: ['screen lock', 'keep awake', 'toggle'], anchor: LOCK_ANCHOR,
      actions: [jump(LOCK_ANCHOR, 'Toggle')]},
    {title: 'Amphetamine idle time', subtitle: 'Minutes before the screen locks', keywords: ['minutes', 'delay'],
      anchor: MINUTES_ANCHOR, actions: [jump(MINUTES_ANCHOR, 'Edit')]},
  ];
}
