// Page entry: builds the nav and sections from one registry, polls, and watches the panel's own health.
import {health, renewToken} from './panel_api.mjs';
import {button, node, refreshTimes} from './panel_dom.mjs';
import {editingReason, markPaused, refresh, register, sections} from './panel_sections.mjs';
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
const STORAGE = {collapsed: 'mac-panel.collapsed', theme: 'mac-panel.theme'};

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

function setCollapsed(section, collapsed) {
  section.classList.toggle('collapsed', collapsed);
  section.querySelector('.toggle').setAttribute('aria-expanded', String(!collapsed));
  const list = new Set(stored.get(STORAGE.collapsed, []));
  if (collapsed) list.add(section.id);
  else list.delete(section.id);
  stored.set(STORAGE.collapsed, [...list]);
}

function buildNav(nav) {
  SECTIONS.forEach(entry => {
    const link = node('a');
    link.href = `#${entry.id}`;
    link.append(node('span', null, 'dot level-idle'), entry.title, node('span', '', 'summary'));
    link.addEventListener('click', () => {
      const section = document.getElementById(entry.id);
      if (section.classList.contains('collapsed')) setCollapsed(section, false);
    });
    nav.append(link);
  });
}

function buildSection(entry, collapsed) {
  const section = node('section');
  section.id = entry.id;
  section.classList.toggle('collapsed', collapsed);
  const head = node('div', null, 'section-head');
  const heading = node('h2');
  const toggle = node('button', entry.title, 'toggle');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', String(!collapsed));
  toggle.setAttribute('aria-controls', `${entry.id}-body`);
  toggle.addEventListener('click', () => setCollapsed(section, !section.classList.contains('collapsed')));
  heading.append(toggle);
  head.append(heading, node('span', '', 'meta'),
    button('Refresh', () => refresh(entry.id), {className: 'refresh-one', title: `Refresh ${entry.title}`}));
  const status = node('p', '', 'notice');
  status.setAttribute('role', 'status');
  const body = node('div', null, 'body');
  body.id = `${entry.id}-body`;
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

// Underline the nav link of the section whose heading last scrolled under the sticky header.
function trackCurrentSection() {
  const links = [...document.querySelectorAll('nav a')];
  let scheduled = false;
  const update = () => {
    scheduled = false;
    const limit = document.querySelector('header').offsetHeight + 24;
    let current = null;
    sections().forEach(entry => {
      if (document.getElementById(entry.id).getBoundingClientRect().top <= limit) current = entry.id;
    });
    links.forEach(link => {
      if (link.getAttribute('href') === `#${current}`) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
  };
  addEventListener('scroll', () => {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(update);
    }
  }, {passive: true});
  update();
}

const collapsedIds = new Set(stored.get(STORAGE.collapsed, []));
register(SECTIONS);
buildNav(document.querySelector('nav'));
const main = document.querySelector('main');
SECTIONS.forEach(entry => main.append(buildSection(entry, collapsedIds.has(entry.id))));
setupTheme(document.getElementById('theme'));
document.getElementById('refresh').addEventListener('click', refreshAll);
refreshAll();
checkHealth();
trackCurrentSection();
setInterval(tick, POLL_MS);
setInterval(() => refreshTimes(), TIME_TICK_MS);
