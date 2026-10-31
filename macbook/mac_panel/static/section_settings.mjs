// Settings forms generated from each tool's declaration; saves go through tool_settings on the server.
import {button, isChanged, node} from './panel_dom.mjs';
import {action, refresh} from './panel_sections.mjs';
import {Level, status, worst} from './status_cell.mjs';

const SECTION = 'settings';
const CONFLICT = 409;

function labelFor(setting) {
  return setting.label || setting.name.replace(/_/g, ' ');
}

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
  const id = `${declaration.tool}-${setting.name}`;
  const input = makeInput(setting, value, id);
  const row = node('div', null, 'setting');
  const text = node('div', null, 'setting-text');
  const label = node('label');
  label.htmlFor = id;
  label.append(node('strong', labelFor(setting)));
  const unit = setting.unit ? ` ${setting.unit}` : '';
  text.append(label, node('small', `${setting.description} Default: ${setting.default}${unit}.`));
  const control = node('div', null, 'control');
  const marker = node('span', '', 'marker');
  control.append(input, node('span', setting.unit || '', 'muted'), marker);
  row.append(text, control);
  return {row, input, marker};
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
    marker.textContent = changed ? 'unsaved' : value === setting.default ? 'default' : '';
  });
  save.disabled = !dirty;
}

function toolForm(tool) {
  const {declaration, state} = tool;
  const form = node('form', null, 'tool');
  form.append(node('h3', declaration.title), node('p', declaration.description, 'muted'));
  const controls = new Map();
  declaration.settings.forEach(setting => {
    const parts = settingRow(declaration, setting, state.values[setting.name]);
    controls.set(setting.name, parts);
    form.append(parts.row);
  });
  if (state.error) form.append(node('p', state.error, 'error'));
  const buttons = node('div', null, 'actions');
  const save = node('button', 'Save');
  save.type = 'submit';
  save.dataset.key = `${declaration.tool}-save`;
  const reload = button('Reload values', () => refresh(SECTION), {key: `${declaration.tool}-reload`});
  reload.hidden = true;
  const defaults = Object.fromEntries(declaration.settings.map(s => [s.name, s.default]));
  const reset = button('Reset to defaults', () => action(SECTION, '/api/settings',
    {tool: declaration.tool, values: defaults, revision: state.revision}),
  {confirm: 'Confirm reset to defaults', key: `${declaration.tool}-reset`});
  buttons.append(save, reset, reload, node('span', state.path, 'muted small'));
  form.append(buttons);
  form.addEventListener('input', () => updateMarkers(declaration, controls, save));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(declaration.settings.map(s => [s.name, readValue(s, controls.get(s.name).input)]));
    save.disabled = true;
    // A failed save keeps the typed values; only a conflict needs the saved values back.
    await action(SECTION, '/api/settings', {tool: declaration.tool, values, revision: state.revision}, {
      reloadOnError: false, onError: error => { reload.hidden = error.status !== CONFLICT; save.disabled = false; },
    });
  });
  updateMarkers(declaration, controls, save);
  return form;
}

// A declaration the server could not load gets a card like a broken automation's: no form, just why.
function errorCard(tool) {
  const root = node('div', null, 'tool');
  const line = node('p', null, 'summary-line');
  line.append(status(Level.ERROR, 'unavailable'));
  root.append(node('h3', tool.tool), line, node('p', tool.error, 'error'));
  return root;
}

function rendered(tool) {
  if (tool.error) return {root: errorCard(tool), level: Level.ERROR, summary: `${tool.tool}: unavailable`};
  const level = tool.state.error ? Level.WARN : Level.IDLE;
  return {root: toolForm(tool), level, summary: tool.declaration.title};
}

export function render(data) {
  const root = node('div');
  const tools = data.tools.map(rendered);
  tools.forEach(tool => root.append(tool.root));
  if (!tools.length) root.append(node('p', 'No tools declare settings'));
  const summary = tools.map(tool => tool.summary).join(' · ') || 'none';
  return {node: root, summary, level: tools.length ? worst(tools.map(tool => tool.level)) : Level.IDLE};
}
