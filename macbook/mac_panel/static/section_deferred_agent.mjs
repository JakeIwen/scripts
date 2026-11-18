// The "Add agent job" form for Deferred jobs: queues `deferred.zsh add-agent` on this Mac or on vanpi.
// The model control and the vanpi-only rows follow the Runs on choice.
import {anchor, button, confirmText, details, fieldError, node, settingRow} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {AGENT_FIELDS, DEFAULT_MODEL, EFFORTS, Host, PI_TIERS, VANPI_ONLY, agentBody, agentProblemsFromError, validateAgent}
  from './deferred_agent_form.mjs';
import {NAME_LIMIT} from './deferred_form.mjs';

const SECTION = 'deferred';
const COPY = JOBS_COPY.deferred.agent;
const UNITS = {timeout: 'seconds', run_timeout: 'minutes'};

function select(choices, value, labels = {}) {
  const result = node('select');
  choices.forEach(choice => {
    const option = node('option', labels[choice] ?? choice);
    option.value = choice;
    option.defaultSelected = choice === value;
    result.append(option);
  });
  result.value = value;
  return result;
}

function textInput(key) {
  const input = node(key === 'prompt' ? 'textarea' : 'input');
  if (key === 'due') input.type = 'datetime-local';
  else if (UNITS[key] || key === 'attempts') {
    input.type = 'number';
    input.min = '1';
  } else if (key === 'prompt') {
    input.rows = 6;
  } else {
    input.autocomplete = 'off';
    if (key === 'name') input.maxLength = NAME_LIMIT;
  }
  if (key !== 'due' && key !== 'name' && key !== 'description') input.spellcheck = false;
  return input;
}

/** The model control for a host: free text on the Mac, the three pi-agent tiers on vanpi. */
export function modelControl(host) {
  const control = host === Host.VANPI ? select(PI_TIERS, DEFAULT_MODEL[host]) : textInput('model');
  if (host !== Host.VANPI) control.placeholder = DEFAULT_MODEL[host];
  return control;
}

function control(key) {
  if (key === 'host') return select(Object.values(Host), Host.MAC, COPY.hosts);
  if (key === 'effort') return select(EFFORTS, 'medium');
  if (key === 'model') return modelControl(Host.MAC);
  return textInput(key);
}

function keyed(input, key) {
  input.id = `deferred-agent-${key}`;
  input.dataset.key = `agent-${key}`;
  return input;
}

export function addAgentForm() {
  const form = node('form', null, 'add-job');
  const inputs = {};
  const rows = {};
  const slots = {};
  AGENT_FIELDS.forEach(key => {
    inputs[key] = keyed(control(key), key);
    slots[key] = node('span', null, 'control-slot');
    slots[key].append(inputs[key]);
    const [label, help] = COPY.fields[key];
    rows[key] = settingRow({label, info: help, control: slots[key], unit: UNITS[key]});
    form.append(rows[key]);
  });
  const followHost = () => {
    const host = inputs.host.value;
    VANPI_ONLY.forEach(key => { rows[key].hidden = host !== Host.VANPI; });
    inputs.model = keyed(modelControl(host), 'model');
    slots.model.replaceChildren(inputs.model);
    inputs.cwd.placeholder = COPY.cwdPlaceholder[host];
  };
  inputs.host.addEventListener('change', followHost);
  followHost();
  const formError = node('div');
  const show = problems => {
    form.querySelectorAll('.field-error').forEach(element => element.remove());
    Object.entries(problems).forEach(([key, message]) => {
      if (rows[key]) rows[key].after(fieldError(message));
      else formError.append(fieldError(message));
    });
  };
  const values = () => Object.fromEntries(AGENT_FIELDS.map(key => {
    const value = inputs[key].value;
    return [key, key === 'prompt' || value.trim() !== '' ? (key === 'name' ? value.trim() : value) : ''];
  }));
  const submit = async () => {
    const current = values();
    const problems = validateAgent(current);
    show(problems);
    if (Object.keys(problems).length) return;
    await action(SECTION, '/api/deferred', agentBody(current), {
      onError: error => show(agentProblemsFromError(error.message)), reloadOnError: false,
    });
  };
  form.addEventListener('submit', event => event.preventDefault());
  form.append(formError, button('Add agent job', submit, {confirm: confirmText('add this agent job'), key: 'add-agent-job'}));
  return anchor(details(COPY.title, form, 'add-agent-form'), 'add-agent-job');
}
