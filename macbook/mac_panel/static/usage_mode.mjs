// The usage-budget mode picker: the current mode and where it came from, one radio per mode, an armed Apply.
import {button, confirmText, node, tag, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {MODE_COPY as COPY} from './section_copy.mjs';

export const MODE_ANCHOR = 'mode';
export const APPLY_KEY = 'mode-apply';
const AUTOMATIC = 'auto';

function option(name, label, text, kind, checked) {
  const row = node('label', null, 'mode-option');
  const input = node('input');
  input.type = 'radio';
  input.name = 'usage-mode';
  input.value = name;
  input.dataset.key = `mode-${name}`;
  input.checked = checked;
  input.defaultChecked = checked;
  const head = node('span', null, 'mode-name');
  head.append(node('strong', label));
  if (kind) head.append(tag(kind));
  row.append(input, head, node('small', text, 'muted'));
  return {row, input};
}

function currentLine(data) {
  const line = node('p', null, 'mode-current');
  line.append(node('strong', data.lean ?? 'unknown'));
  if (data.mode_source) line.append(' ', tag(data.mode_source));
  if (data.expires_at) line.append(' expires ', timeNode(data.expires_at));
  return line;
}

// Apply is rebuilt per selection because a two-click button's confirmation wording is fixed when it is made.
function applyButton(mode, send) {
  return button(COPY.apply, () => send(mode), {confirm: confirmText(COPY.confirm(mode)), key: APPLY_KEY});
}

export function modeBlock(data, section) {
  const root = node('div', null, 'mode-block');
  root.append(currentLine(data));
  const manual = data.mode_source !== 'auto' && data.lean in COPY.modes;
  const selected = manual ? data.lean : AUTOMATIC;
  const list = node('div', null, 'mode-options');
  const send = mode => action(section, '/api/usage', {action: 'mode', mode, confirmed: true});
  let apply = applyButton(selected, send);
  const choose = mode => { const next = applyButton(mode, send); apply.replaceWith(next); apply = next; };
  const choices = [
    ...Object.entries(COPY.modes).map(([name, spec]) => [name, name, spec.text, spec.kind]),
    [AUTOMATIC, COPY.automatic.label, COPY.automatic.text, null],
  ];
  choices.forEach(([name, label, text, kind]) => {
    const {row, input} = option(name, label, text, kind, name === selected);
    input.addEventListener('change', () => choose(name));
    list.append(row);
  });
  const actions = node('div', null, 'actions');
  actions.append(apply);
  root.append(list, actions);
  return root;
}
