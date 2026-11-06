// A tool's settings card (fields, Save, Reset, revision-conflict Reload), shared by every section that shows one.
import {button, card, fieldError, isChanged, node, settingRow as row} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {SETTINGS_COPY as COPY} from './section_copy.mjs';
import {settingAnchor, settingLabel} from './settings_fields.mjs';

const CONFLICT = 409;

function makeInput(setting, value, id) {
  let input;
  if (setting.type === 'choice') {
    input = node('select');
    setting.choices.forEach(choice => {
      const option = node('option', choice);
      option.value = choice;
      option.defaultSelected = choice === String(value);
      input.append(option);
    });
    input.value = value;
  } else if (setting.type === 'boolean') {
    input = node('input');
    input.type = 'checkbox';
    input.checked = value;
    input.defaultChecked = value;
  } else {
    input = node('input');
    input.type = 'number';
    input.required = true;
    input.step = setting.step ?? (setting.type === 'integer' ? 1 : 'any');
    if (setting.minimum !== null && setting.minimum !== undefined) input.min = setting.minimum;
    if (setting.maximum !== null && setting.maximum !== undefined) input.max = setting.maximum;
    input.value = value;
    input.defaultValue = String(value);
  }
  input.id = id;
  input.dataset.key = id;
  return input;
}

function readValue(setting, input) {
  if (setting.type === 'boolean') return input.checked;
  return ['integer', 'number'].includes(setting.type) ? Number(input.value) : input.value;
}

function settingRow(declaration, setting, value) {
  const id = settingAnchor(declaration.tool, setting.name);
  const input = makeInput(setting, value, id);
  const marker = node('span', '', 'marker');
  const unit = setting.unit ? String(setting.unit) : undefined;
  const help = `${setting.description} Default: ${setting.default}${unit ? ` ${unit}` : ''}.`;
  return {row: row({label: settingLabel(setting), info: help, control: input, unit, marker, anchor: id}), input, marker};
}

// Markers say what will happen on Save: nothing, a return to the default, or a new value.
function updateMarkers(declaration, controls, save) {
  let dirty = false;
  declaration.settings.forEach(setting => {
    const {input, marker} = controls.get(setting.name);
    const changed = isChanged(input);
    dirty = dirty || changed;
    marker.classList.toggle('changed', changed);
    const value = readValue(setting, input);
    marker.textContent = changed ? 'unsaved' : value === setting.default ? 'default' : 'changed from default';
  });
  save.disabled = !dirty;
}

/** One tool's settings form as a card; `sectionId` owns its polling pause, notices and POSTs. */
export function toolCard(tool, sectionId) {
  const {declaration, state} = tool;
  const form = node('form');
  form.append(node('p', declaration.description, 'muted'));
  const controls = new Map();
  declaration.settings.forEach(setting => {
    const parts = settingRow(declaration, setting, state.values[setting.name]);
    controls.set(setting.name, parts);
    form.append(parts.row);
  });
  if (state.error) form.append(fieldError(state.error));
  const buttons = node('div', null, 'actions');
  const save = node('button', 'Save');
  save.type = 'submit';
  save.dataset.key = `${declaration.tool}-save`;
  const reload = button('Reload values', () => refresh(sectionId), {key: `${declaration.tool}-reload`});
  reload.hidden = true;
  const defaults = Object.fromEntries(declaration.settings.map(s => [s.name, s.default]));
  const reset = button('Reset to defaults', () => action(sectionId, '/api/settings',
    {tool: declaration.tool, action: 'reset', values: defaults, revision: state.revision, confirmed: true}),
  {confirm: 'Confirm: reset to defaults', title: COPY.resetInfo, key: `${declaration.tool}-reset`});
  buttons.append(save, reset, reload, node('span', state.path, 'muted small'));
  form.append(buttons);
  form.addEventListener('input', () => updateMarkers(declaration, controls, save));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(declaration.settings.map(s => [s.name, readValue(s, controls.get(s.name).input)]));
    save.disabled = true;
    // A failed save keeps the typed values; only a conflict needs the saved values back.
    await action(sectionId, '/api/settings', {tool: declaration.tool, values, revision: state.revision}, {
      reloadOnError: false, onError: error => { reload.hidden = error.status !== CONFLICT; save.disabled = false; },
    });
  });
  updateMarkers(declaration, controls, save);
  return card({title: declaration.title, body: form});
}
