// Validation and request shaping for the Add agent job form; pure, so Node can test it. Mirrors
// deferred_adapter.add_agent, which checks everything again before anything is staged.
import {NAME, NAME_PROBLEM} from './deferred_form.mjs';

export const Host = Object.freeze({MAC: 'mac', VANPI: 'vanpi'});
export const PI_TIERS = ['astra', 'sol', 'luna'];
export const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'];
export const DEFAULT_MODEL = {[Host.MAC]: 'haiku', [Host.VANPI]: 'sol'};
export const AGENT_FIELDS = ['host', 'due', 'name', 'description', 'prompt', 'pi_prompt_path', 'model', 'effort',
  'cwd', 'ready', 'attempts', 'timeout', 'run_timeout'];
// Rows shown only for vanpi jobs.
export const VANPI_ONLY = ['pi_prompt_path', 'run_timeout'];
const MAC_MODEL = /^[a-z0-9][a-z0-9.[\]-]{0,63}$/;
const PATH_LIMIT = 1024;
const READY_LIMIT = 500;
// deferred.zsh refuses a shorter vanpi --timeout: a pass must launch and read the run once (deferred_adapter.PI_MIN_TIMEOUT).
export const PI_MIN_TIMEOUT = 390;
const NUMBERS = ['attempts', 'timeout', 'run_timeout'];
// The server's wording per field, tried in order (run_timeout before timeout, the Pi path before prompt).
const SERVER_FIELD = [['due', /\bdue\b/i], ['name', /\bjob name\b|\bname must\b/i], ['run_timeout', /run_timeout|run-timeout/i],
  ['attempts', /\battempts\b/i], ['timeout', /\btimeout\b/i], ['pi_prompt_path', /Pi prompt path|vanpi: prompt/i],
  ['prompt', /\bprompt\b/i], ['model', /\bmodel\b/i], ['effort', /\beffort\b/i], ['cwd', /\bcwd\b|working directory/i],
  ['ready', /\bready\b/i], ['host', /\bhost\b/i], ['description', /\bdescription\b/i]];

const positive = text => /^\d+$/.test(text) && Number(text) > 0;
const oneLine = (text, limit) => !/[\0\r\n]/.test(text) && text.length <= limit;
const absolutePath = text => text.startsWith('/') && oneLine(text, PATH_LIMIT);
const isVanpi = values => values.host === Host.VANPI;

function promptProblems(values, problems) {
  const hasText = values.prompt.trim() !== '';
  const hasPath = isVanpi(values) && values.pi_prompt_path !== '';
  if (hasText && hasPath) problems.prompt = 'Paste the prompt or give a Pi prompt file, not both.';
  else if (!hasText && !hasPath) problems.prompt = isVanpi(values) ? 'Paste the prompt or give a Pi prompt file.' : 'Paste the prompt.';
  if (values.prompt.includes('\0')) problems.prompt = 'The prompt cannot contain NUL characters.';
  if (hasPath && !absolutePath(values.pi_prompt_path)) problems.pi_prompt_path = `An absolute path on vanpi, one line, at most ${PATH_LIMIT} characters.`;
}

/** values: the form's strings. Returns {field: message} for what is wrong, empty when the form can be sent. */
export function validateAgent(values) {
  const problems = {};
  if (!Object.values(Host).includes(values.host)) problems.host = 'Choose where the prompt runs.';
  if (!values.due) problems.due = 'Choose when the job is due.';
  if (!NAME.test(values.name)) problems.name = NAME_PROBLEM;
  promptProblems(values, problems);
  if (values.model && !(isVanpi(values) ? PI_TIERS.includes(values.model) : MAC_MODEL.test(values.model))) {
    problems.model = isVanpi(values) ? `Choose ${PI_TIERS.join(', ')}.` : 'A Claude model name: lower-case letters, digits, dots, hyphens and brackets.';
  }
  if (!EFFORTS.includes(values.effort)) problems.effort = 'Choose an effort.';
  if (values.cwd && !absolutePath(values.cwd)) problems.cwd = `An absolute path, one line, at most ${PATH_LIMIT} characters.`;
  if (values.ready && !oneLine(values.ready, READY_LIMIT)) problems.ready = `One line, at most ${READY_LIMIT} characters.`;
  NUMBERS.forEach(key => {
    if (key === 'run_timeout' && !isVanpi(values)) return;
    if (values[key] && !positive(values[key])) problems[key] = 'Must be a positive whole number.';
  });
  if (isVanpi(values) && !problems.timeout && values.timeout && Number(values.timeout) < PI_MIN_TIMEOUT) {
    problems.timeout = `On vanpi, at least ${PI_MIN_TIMEOUT} seconds: a check must launch the run and read it once.`;
  }
  return problems;
}

/** The request body for values that passed validateAgent; empty optional fields leave the CLI default. */
export function agentBody(values) {
  const body = {action: 'add-agent', host: values.host, name: values.name, due: values.due.replace('T', ' '),
    description: values.description, effort: values.effort, confirmed: true};
  if (isVanpi(values) && values.pi_prompt_path) body.pi_prompt_path = values.pi_prompt_path;
  else body.prompt = values.prompt;
  ['model', 'cwd', 'ready'].forEach(key => { if (values[key]) body[key] = values[key]; });
  NUMBERS.forEach(key => {
    if (values[key] && (key !== 'run_timeout' || isVanpi(values))) body[key] = Number(values[key]);
  });
  return body;
}

/** Maps a server error message to {field: message}; a message that names no field comes back under `form`. */
export function agentProblemsFromError(message) {
  const match = SERVER_FIELD.find(([, pattern]) => pattern.test(message));
  return {[match ? match[0] : 'form']: message};
}
