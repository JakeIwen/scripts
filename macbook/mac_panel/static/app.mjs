// Page entry: builds the tabs and sections from one registry, polls, and watches the panel's own health.
import {health, renewToken} from './panel_api.mjs';
import {button, node, refreshTimes} from './panel_dom.mjs';
import {editingReason, loadedAge, markPaused, refresh, register, sections} from './panel_sections.mjs';
import {absoluteText} from './relative_time.mjs';
import * as crontab from './section_crontab.mjs';
import * as deferred from './section_deferred.mjs';
import * as guard from './section_guard.mjs';
import * as ntfy from './section_ntfy.mjs';
import * as restore from './section_restore.mjs';
import * as services from './section_services.mjs';
import * as settings from './section_settings.mjs';

// Order follows how often the state changes and matters; `auto` sections poll every 15 s.
const SECTIONS = [
  {id: 'deferred', title: 'Deferred jobs', render: deferred.render, auto: true},
  {id: 'services', title: 'Background services', render: services.render, auto: true},
  {id: 'crontab', title: 'User crontab', render: crontab.render, auto: false},
  {id: 'llm-restore', title: 'llm-restore', render: restore.render, auto: false},
  {id: 'guard', title: 'BTT guard', render: guard.render, auto: false},
  {id: 'ntfy', title: 'ntfy alerts', render: ntfy.render, auto: false},
  {id: 'settings', title: 'Settings', render: settings.render, auto: false},
];
const POLL_MS = 15000;
const TIME_TICK_MS = 5000;
const STORAGE = {tab: 'mac-panel.tab', theme: 'mac-panel.theme'};

// Per-viewer conveniences only; the page renders the same without them.
const stored = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked: nothing to keep */ }
  },
};

const tabOf = id => document.getElementById(`tab-${id}`);
const knownId = id => SECTIONS.some(entry => entry.id === id) ? id : null;

// The hash names the open tab, so links such as crontab's "#llm-restore" and bookmarks open the right one.
function selectTab(id) {
  SECTIONS.forEach(entry => {
    const selected = entry.id === id;
    tabOf(entry.id).setAttribute('aria-selected', String(selected));
    tabOf(entry.id).tabIndex = selected ? 0 : -1;
    document.getElementById(entry.id).hidden = !selected;
  });
  stored.set(STORAGE.tab, id);
  if (location.hash !== `#${id}`) history.replaceState(null, '', `#${id}`);
}

// Sections without auto-refresh reload when opened after a poll interval, so an old tab is never shown as current.
function openTab(id) {
  if (tabOf(id).getAttribute('aria-selected') === 'true') return;
  selectTab(id);
  scrollTo({top: 0});
  const entry = SECTIONS.find(item => item.id === id);
  if (!entry.auto && loadedAge(id) > POLL_MS) refreshIfIdle(entry);
}

const TAB_KEYS = {ArrowRight: 1, ArrowLeft: -1, Home: -Infinity, End: Infinity};

function moveTab(event) {
  const step = TAB_KEYS[event.key];
  if (step === undefined) return;
  event.preventDefault();
  const current = SECTIONS.findIndex(entry => tabOf(entry.id) === document.activeElement);
  const index = Number.isFinite(step) ? (current + step + SECTIONS.length) % SECTIONS.length
    : step < 0 ? 0 : SECTIONS.length - 1;
  openTab(SECTIONS[index].id);
  tabOf(SECTIONS[index].id).focus();
}

function buildTabs(list) {
  SECTIONS.forEach(entry => {
    const tab = node('button', null, 'tab');
    tab.type = 'button';
    tab.id = `tab-${entry.id}`;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', entry.id);
    tab.append(node('span', null, 'dot level-idle'), entry.title);
    tab.addEventListener('click', () => openTab(entry.id));
    list.append(tab);
  });
  list.addEventListener('keydown', moveTab);
}

function buildSection(entry) {
  const section = node('section');
  section.id = entry.id;
  section.hidden = true;
  section.setAttribute('role', 'tabpanel');
  section.setAttribute('aria-labelledby', `tab-${entry.id}`);
  const head = node('div', null, 'section-head');
  head.append(node('span', '', 'meta'),
    button('Refresh', () => refresh(entry.id), {className: 'refresh-one', title: `Refresh ${entry.title}`}));
  const status = node('p', '', 'notice');
  status.setAttribute('role', 'status');
  const body = node('div', null, 'body');
  const content = node('div', null, 'content');
  content.append(node('p', 'Loading…', 'loading'));
  body.append(content);
  section.append(head, status, body);
  return section;
}

function applyTheme(value) {
  if (value === 'light' || value === 'dark') document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
}

function setupTheme(select) {
  const saved = stored.get(STORAGE.theme, 'system');
  select.value = saved;
  applyTheme(select.value);
  select.addEventListener('change', () => {
    stored.set(STORAGE.theme, select.value);
    applyTheme(select.value);
  });
}

function refreshIfIdle(entry) {
  const reason = editingReason(entry.id);
  if (reason) {
    markPaused(entry.id, reason);
    return null;
  }
  return refresh(entry.id);
}

function refreshAll() {
  return Promise.all(sections().map(refreshIfIdle));
}

let instance = null;
let unreachableSince = null;

function setConnection(text, kind, extra) {
  const element = document.getElementById('connection');
  if (element.dataset.kind === kind && element.textContent.startsWith(text) && extra) return;
  element.dataset.kind = kind;
  element.className = kind;
  element.replaceChildren(text);
  if (extra) element.append(extra);
}

async function checkHealth() {
  try {
    const current = await health();
    unreachableSince = null;
    if (instance && current.instance !== instance) {
      instance = current.instance;
      setConnection('Panel restarted; refreshing', '');
      await renewToken();
      refreshAll();
      return;
    }
    instance = current.instance;
    if (current.source_changed) {
      setConnection('Panel code changed since it started', 'warn',
        button('Restart panel', () => services.restartPanel(services.PANEL_LABEL), {confirm: 'Confirm restart panel'}));
    } else {
      setConnection('', '');
    }
  } catch {
    unreachableSince ??= Date.now();
    setConnection(`Panel unreachable since ${absoluteText(unreachableSince)}`, 'error');
  }
}

function tick() {
  checkHealth();
  sections().filter(entry => entry.auto).forEach(refreshIfIdle);
}

register(SECTIONS);
buildTabs(document.querySelector('[role="tablist"]'));
const main = document.querySelector('main');
SECTIONS.forEach(entry => main.append(buildSection(entry)));
selectTab(knownId(location.hash.slice(1)) ?? knownId(stored.get(STORAGE.tab, null)) ?? SECTIONS[0].id);
addEventListener('hashchange', () => {
  const id = knownId(location.hash.slice(1));
  if (id) openTab(id);
});
setupTheme(document.getElementById('theme'));
document.getElementById('refresh').addEventListener('click', refreshAll);
refreshAll();
checkHealth();
setInterval(tick, POLL_MS);
setInterval(() => refreshTimes(), TIME_TICK_MS);
