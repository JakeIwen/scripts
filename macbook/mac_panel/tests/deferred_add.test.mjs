import assert from 'node:assert/strict';
import test from 'node:test';

import {addBody, problemsFromError, validateAdd} from '../static/deferred_form.mjs';

const good = {due: '2026-11-06T09:30', name: 'rotate-keys', description: '', script: '#!/bin/zsh\necho hi\n', attempts: '', timeout: ''};

test('a complete form has no problems', () => {
  assert.deepEqual(validateAdd(good), {});
});

test('each bad field is named', () => {
  const problems = validateAdd({...good, due: '', name: 'Bad Name', script: 'echo', attempts: '0', timeout: 'x'});
  assert.deepEqual(Object.keys(problems).sort(), ['attempts', 'due', 'name', 'script', 'timeout']);
});

test('the body uses the server date format and numbers, and always confirms', () => {
  const body = addBody({...good, attempts: '3', timeout: '120'});
  assert.equal(body.due, '2026-11-06 09:30');
  assert.equal(body.attempts, 3);
  assert.equal(body.timeout, 120);
  assert.equal(body.confirmed, true);
  assert.equal('attempts' in addBody(good), false);
});

test('server messages map to the field they name', () => {
  assert.deepEqual(Object.keys(problemsFromError('Invalid job name')), ['name']);
  assert.deepEqual(Object.keys(problemsFromError('Due time must be YYYY-MM-DD HH:MM')), ['due']);
  assert.deepEqual(Object.keys(problemsFromError('Script must start with #! and text cannot contain NUL')), ['script']);
  assert.deepEqual(Object.keys(problemsFromError('timeout must be positive')), ['timeout']);
  assert.deepEqual(Object.keys(problemsFromError('Something else broke')), ['form']);
});
