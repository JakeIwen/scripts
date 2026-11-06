// The "Add job" form for Deferred jobs. Checks mirror deferred_adapter.add so most mistakes are caught before the
// request; whatever the server still refuses is shown against the field its message names.
import {anchor, button, confirmText, details, fieldError, node, settingRow} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {FIELDS, addBody, problemsFromError, validateAdd} from './deferred_form.mjs';

const SECTION = 'deferred';
const COPY = JOBS_COPY.deferred;
function control(key) {
  const input = node(key === 'script' ? 'textarea' : 'input');
  input.id = `deferred-add-${key}`;
  input.dataset.key = `add-${key}`;
  if (key === 'due') input.type = 'datetime-local';
  else if (key === 'attempts' || key === 'timeout') {
    input.type = 'number';
    input.min = '1';
  } else if (key === 'name') {
    input.maxLength = 60;
    input.autocomplete = 'off';
  } else if (key === 'script') {
    input.rows = 8;
    input.spellcheck = false;
    input.value = COPY.defaultScript;
    input.defaultValue = COPY.defaultScript;
  }
  return input;
}

export function addJobForm() {
  const form = node('form', null, 'add-job');
  const inputs = {};
  const rows = {};
  FIELDS.forEach(key => {
    inputs[key] = control(key);
    const [label, help] = COPY.fields[key];
    rows[key] = settingRow({label, info: help, control: inputs[key], unit: key === 'timeout' ? 'seconds' : undefined});
    form.append(rows[key]);
  });
  const formError = node('div');
  const show = problems => {
    form.querySelectorAll('.field-error').forEach(element => element.remove());
    Object.entries(problems).forEach(([key, message]) => {
      if (rows[key]) rows[key].after(fieldError(message));
      else formError.append(fieldError(message));
    });
  };
  const values = () => Object.fromEntries(FIELDS.map(key => [key, inputs[key].value.trim() === '' ? '' : inputs[key].value]));
  const submit = async () => {
    const current = values();
    const problems = validateAdd({...current, name: current.name.trim(), script: current.script});
    show(problems);
    if (Object.keys(problems).length) return;
    await action(SECTION, '/api/deferred', addBody({...current, name: current.name.trim()}), {
      onError: error => show(problemsFromError(error.message)), reloadOnError: false,
    });
  };
  form.addEventListener('submit', event => event.preventDefault());
  form.append(formError, button('Add job', submit, {confirm: confirmText('add this job'), key: 'add-job'}));
  // Collapsed until wanted; the open state survives refreshes through the disclosure key.
  return anchor(details(COPY.addTitle, form, 'add-form'), 'add-job');
}
