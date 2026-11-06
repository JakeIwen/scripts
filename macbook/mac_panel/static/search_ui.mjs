// Header search: results popover under the input, arrow/Enter/Escape, and Cmd-K / Ctrl-K from anywhere.
import {node} from './panel_dom.mjs';
import {sections, state} from './panel_sections.mjs';
import {buildIndex, search} from './search.mjs';
import {GROUPS, groupOf} from './section_copy.mjs';
import {goTo} from './shell.mjs';

const KIND_LABEL = {section: 'Section', group: 'Group', action: 'Action', item: '', setting: 'Setting'};

function liveIndex() {
  const entries = sections().map(entry => ({
    id: entry.id, title: entry.title, group: groupOf(entry.id)?.title, searchItems: entry.searchItems,
  }));
  return buildIndex({sections: entries, groups: GROUPS, payloadOf: id => state[id]?.payload});
}

function resultRow(item, index) {
  const row = node('div', null, 'search-result');
  row.id = `search-result-${index}`;
  row.setAttribute('role', 'option');
  row.dataset.index = index;
  const text = node('div', null, 'search-text');
  text.append(node('span', item.title, 'search-title'));
  if (item.subtitle) text.append(node('span', item.subtitle, 'search-subtitle'));
  row.append(text);
  const kind = KIND_LABEL[item.kind];
  if (kind) row.append(node('span', kind, 'tag'));
  (item.actions ?? []).forEach((act, number) => {
    const run = node('button', act.label, 'secondary');
    run.type = 'button';
    run.dataset.action = number;
    row.append(run);
  });
  return row;
}

export function setupSearch() {
  const input = document.getElementById('search');
  const list = document.getElementById('search-results');
  let shown = [];
  let active = -1;

  const rows = () => [...list.querySelectorAll('.search-result')];
  function close() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    active = -1;
  }
  function highlight(index) {
    active = index;
    rows().forEach((row, at) => row.classList.toggle('active', at === index));
    if (index >= 0) {
      input.setAttribute('aria-activedescendant', `search-result-${index}`);
      rows()[index].scrollIntoView({block: 'nearest'});
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }
  function open(item) {
    close();
    input.value = '';
    input.blur();
    goTo(item.section, item.anchor);
  }
  function render() {
    shown = search(liveIndex(), input.value);
    list.replaceChildren(...shown.map(resultRow));
    if (!shown.length && input.value.trim()) list.append(node('div', 'No matches', 'search-empty'));
    list.hidden = !input.value.trim();
    input.setAttribute('aria-expanded', String(!list.hidden));
    highlight(shown.length ? 0 : -1);
  }

  input.addEventListener('input', render);
  input.addEventListener('focus', () => { if (input.value.trim()) render(); });
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (shown.length) highlight((active + (event.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length);
    } else if (event.key === 'Enter' && shown[active]) {
      event.preventDefault();
      open(shown[active]);
    } else if (event.key === 'Escape') {
      if (input.value) input.value = '';
      close();
      input.blur();
    }
  });
  // mousedown (not click) so the input keeps focus long enough for the choice to register.
  list.addEventListener('mousedown', event => event.preventDefault());
  list.addEventListener('click', event => {
    const row = event.target.closest('.search-result');
    if (!row) return;
    const item = shown[Number(row.dataset.index)];
    const run = event.target.closest('button[data-action]');
    if (run) {
      close();
      input.value = '';
      item.actions[Number(run.dataset.action)].run();
    } else {
      open(item);
    }
  });
  input.addEventListener('blur', close);
  document.addEventListener('keydown', event => {
    if (event.key.toLowerCase() !== 'k' || !(event.metaKey || event.ctrlKey)) return;
    event.preventDefault();
    input.focus();
    input.select();
  });
}
