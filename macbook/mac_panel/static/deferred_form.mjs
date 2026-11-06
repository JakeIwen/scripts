// Validation and request shaping for the Add job form; pure, so Node can test it. Mirrors deferred_adapter.add.
const NAME = /^[a-z0-9][a-z0-9-]{0,59}$/;
export const FIELDS = ['due', 'name', 'description', 'script', 'attempts', 'timeout'];
// The server's wording per field, tried in order; `script` also covers its NUL-byte message.
const SERVER_FIELD = [['due', /\bdue\b/i], ['name', /\bname\b/i], ['attempts', /\battempts\b/i],
  ['timeout', /\btimeout\b/i], ['script', /\bscript\b|#!|NUL/i], ['description', /\bdescription\b/i]];

const positive = text => /^\d+$/.test(text) && Number(text) > 0;

/** values: the form's strings. Returns {field: message} for what is wrong, empty when the form can be sent. */
export function validateAdd(values) {
  const problems = {};
  if (!values.due) problems.due = 'Choose when the job is due.';
  if (!NAME.test(values.name)) problems.name = 'Use lower-case letters, digits and hyphens, starting with a letter or digit (60 characters at most).';
  if (!values.script.startsWith('#!')) problems.script = 'The script must start with a #! line.';
  ['attempts', 'timeout'].forEach(key => {
    if (values[key] && !positive(values[key])) problems[key] = `${key[0].toUpperCase()}${key.slice(1)} must be a positive whole number.`;
  });
  return problems;
}

/** The request body for values that passed validateAdd. */
export function addBody(values) {
  const body = {action: 'add', name: values.name, due: values.due.replace('T', ' '), description: values.description,
    script: values.script, confirmed: true};
  ['attempts', 'timeout'].forEach(key => { if (values[key]) body[key] = Number(values[key]); });
  return body;
}

/** Maps a server error message to {field: message}; a message that names no field comes back under `form`. */
export function problemsFromError(message) {
  const match = SERVER_FIELD.find(([, pattern]) => pattern.test(message));
  return {[match ? match[0] : 'form']: message};
}

