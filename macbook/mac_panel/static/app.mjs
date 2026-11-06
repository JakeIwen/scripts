// Page entry: one registry feeds the shell, polling and the state registry; also watches the panel's health.
import {health, renewToken} from './panel_api.mjs';
import {button, refreshTimes} from './panel_dom.mjs';
import {loadedAge, onState, refresh, refreshIfIdle, refreshPending, register, sections} from './panel_sections.mjs';
import {absoluteText} from './relative_time.mjs';
import * as amphetamine from './section_amphetamine.mjs';
import * as automations from './section_automations.mjs';
import {DEFAULT_SECTION, sectionTitle} from './section_copy.mjs';
import * as crontab from './section_crontab.mjs';
import * as deferred from './section_deferred.mjs';
import * as guard from './section_guard.mjs';
import * as metered from './section_metered.mjs';
import * as ntfy from './section_ntfy.mjs';
import * as overview from './section_overview.mjs';
import * as restore from './section_restore.mjs';
import * as services from './section_services.mjs';
import * as settings from './section_settings.mjs';
import * as usage from './section_usage.mjs';
import {setupSearch} from './search_ui.mjs';
import {buildShell, setConnection} from './shell.mjs';

// `auto` sections poll every 15 s. `endpoint: null` means the section renders from the state registry.
// Optional module exports load, searchItems, attentionItems and cardExtra join search and Overview when present.
const MODULES = [
  ['overview', overview, {endpoint: null, auto: false}],
  ['services', services, {auto: true}],
  ['deferred', deferred, {auto: true}],
  ['crontab', crontab, {auto: false}],
  ['automations', automations, {auto: true}],
  ['llm-restore', restore, {auto: false}],
  ['metered', metered, {auto: true}],
  ['amphetamine', amphetamine, {auto: false}],
  ['guard', guard, {auto: false}],
  ['ntfy', ntfy, {auto: false}],
  ['usage', usage, {auto: false}],
  ['settings', settings, {auto: false}],
];
const SECTIONS = MODULES.map(([id, module, options]) => ({
  id, title: sectionTitle(id), render: module.render, load: module.load, searchItems: module.searchItems,
  attentionItems: module.attentionItems, cardExtra: module.cardExtra, ...options,
}));
const POLL_MS = 15000;
const TIME_TICK_MS = 5000;
const OVERVIEW_DEBOUNCE_MS = 300;

function refreshAll() {
  return Promise.all(sections().map(entry => refreshIfIdle(entry.id)));
}

// Sections without auto-refresh reload when opened after a poll interval, so an old view is never shown as current.
function onOpen(id) {
  const entry = sections().find(item => item.id === id);
  if (id === 'overview' || (!entry.auto && loadedAge(id) > POLL_MS)) refreshIfIdle(entry.id);
}

// Overview redraws from the registry shortly after any other section changes, batching a burst of loads.
function keepOverviewCurrent() {
  let timer = null;
  onState(id => {
    if (id === 'overview') return;
    clearTimeout(timer);
    timer = setTimeout(() => refreshIfIdle(sections()[0].id), OVERVIEW_DEBOUNCE_MS);
  });
}

let instance = null;
let unreachableSince = null;

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
        button('Restart panel', () => services.restartPanel(services.PANEL_LABEL), {confirm: 'Confirm: restart panel'}));
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
  sections().filter(entry => entry.auto || refreshPending(entry.id)).forEach(entry => refreshIfIdle(entry.id));
}

register(SECTIONS);
buildShell({ids: SECTIONS.map(entry => entry.id), onOpen, onRefresh: id => refresh(id), fallback: DEFAULT_SECTION});
setupSearch();
keepOverviewCurrent();
document.getElementById('refresh').addEventListener('click', refreshAll);
refreshAll();
checkHealth();
setInterval(tick, POLL_MS);
setInterval(() => refreshTimes(), TIME_TICK_MS);
