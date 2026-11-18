// Page chrome: grouped sidebar nav (a drawer below 900 px), section frames, hash routing, theme and
// the header's health line. Sections themselves know nothing of it.
import {button, info, node} from './panel_dom.mjs';
import {isLoading, onState, state} from './panel_sections.mjs';
import {GROUPS, SECTION_COPY, sectionTitle} from './section_copy.mjs';
import {Level, worst} from './status_cell.mjs';

const STORAGE = {section: 'mac-panel.tab', theme: 'mac-panel.theme'};
const FLASH_MS = 2000;
const NARROW = matchMedia('(max-width: 899px)');

// Per-viewer conveniences only; the page renders the same without them.
export const stored = {
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
export const storedSection = () => stored.get(STORAGE.section, null);

let known = [];
let afterOpen = () => {};
let pendingAnchor = null;
const navLink = id => document.querySelector(`#nav a[data-nav="${CSS.escape(id)}"]`);
const knownId = id => known.includes(id) ? id : null;
// Sections drawn inside another section instead of having a nav entry or a page of their own; the table also
// maps the id that old bookmarks, stored tabs and links used to the section that now shows it.
const HOSTED = {'llm-restore': {id: 'usage', anchor: 'snapshots'}};
/**
 * Maps a hash or stored id to {id, anchorId, frame} of a known nav section, following the hosting table; else null.
 * `frame` is the frame that draws the anchor: the hosted one a hosted id names, else the section.
 */
export function resolveSection(raw, ids = known) {
  const host = HOSTED[raw];
  const id = host?.id ?? raw;
  return ids.includes(id) ? {id, anchorId: host?.anchor, frame: host ? raw : id} : null;
}
/** Ids of the sections shown inside the given one. */
export const hostedIn = id => Object.keys(HOSTED).filter(key => HOSTED[key].id === id);
export const currentSection = () => document.querySelector('main > section:not([hidden])')?.id ?? null;

// ---- section frames ----

function frame(id, onRefresh) {
  const section = node('section', null, HOSTED[id] ? 'hosted' : undefined);
  section.id = id;
  section.hidden = true;
  section.setAttribute('aria-labelledby', `${id}-title`);
  const head = node('div', null, 'section-head');
  const title = node('h2', sectionTitle(id));
  title.id = `${id}-title`;
  head.append(title);
  if (SECTION_COPY[id]?.info) head.append(info(SECTION_COPY[id].info));
  head.append(node('span', '', 'meta'));
  const refreshOne = button('Refresh', () => onRefresh(id),
    {className: 'refresh-one', title: `Refresh ${sectionTitle(id)}`});
  head.append(refreshOne);
  const notice = node('p', '', 'notice');
  notice.setAttribute('role', 'status');
  const body = node('div', null, 'body');
  const content = node('div', null, 'content');
  content.append(node('p', 'Loading…', 'loading'));
  body.append(content);
  section.append(head, notice, body);
  return section;
}

// ---- nav ----

function navGroup(group, ids) {
  const wrap = node('div', null, 'nav-group');
  if (ids.length > 1) {
    const heading = node('div', null, 'nav-heading');
    heading.dataset.group = group.id;
    heading.append(node('span', null, 'dot level-unknown'), group.title);
    wrap.append(heading);
  }
  ids.forEach(id => {
    const link = node('a', null, 'nav-link');
    link.href = `#${id}`;
    link.dataset.nav = id;
    link.append(node('span', null, 'dot level-unknown'), sectionTitle(id));
    link.addEventListener('click', event => {
      event.preventDefault();
      goTo(id);
    });
    wrap.append(link);
  });
  return wrap;
}

function updateDots(changed, entry) {
  const id = HOSTED[changed]?.id ?? changed;
  const link = navLink(id);
  if (link) {
    // A hosted section's problems show on its host's dot; the tooltip stays the host's own summary.
    const levels = [id, ...hostedIn(id)].filter(member => state[member]).map(member => state[member].level);
    link.querySelector('.dot').className = `dot level-${worst(levels)}`;
    link.title = state[id]?.summary || '';
  }
  const group = GROUPS.find(item => item.sections.includes(id));
  if (!group) return;
  const heading = document.querySelector(`.nav-heading[data-group="${CSS.escape(group.id)}"]`);
  if (!heading) return;
  const levels = group.sections.flatMap(member => [member, ...hostedIn(member)]).filter(member => state[member]).map(member => state[member].level);
  heading.querySelector('.dot').className = `dot level-${levels.length ? worst(levels) : Level.UNKNOWN}`;
}

function moveFocus(event) {
  const links = [...document.querySelectorAll('#nav a')];
  const index = links.indexOf(document.activeElement);
  const target = {ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: links.length - 1}[event.key];
  if (target === undefined || index < 0) return;
  event.preventDefault();
  links[(target + links.length) % links.length].focus();
}

// ---- drawer ----

const menu = () => document.getElementById('menu');

function setDrawer(open, returnFocus) {
  document.body.classList.toggle('nav-open', open);
  document.getElementById('backdrop').hidden = !open;
  menu().setAttribute('aria-expanded', String(open));
  if (open) navLink(currentSection())?.focus();
  else if (returnFocus) menu().focus();
}

const drawerOpen = () => document.body.classList.contains('nav-open');

// ---- routing ----

/** Scrolls to a row inside a section and flashes it; with `focus`, moves focus to its first control. */
export function revealAnchor(id, anchorId, {focus = false} = {}) {
  const target = document.querySelector(`#${CSS.escape(id)} [data-anchor="${CSS.escape(anchorId)}"]`);
  if (!target) return false;
  for (let parent = target.closest('details'); parent; parent = parent.parentElement?.closest('details')) parent.open = true;
  target.scrollIntoView({block: 'center'});
  target.classList.add('flash');
  setTimeout(() => target.classList.remove('flash'), FLASH_MS);
  if (focus) target.querySelector('button:not(:disabled), input, select, a')?.focus({preventScroll: true});
  return true;
}

// The watch of the latest bookmark landing; opening any section ends it.
let stopRevealing = () => {};

/** The frame that draws a row: the hosted frame whose bookmark anchor it is, else the section itself. */
const anchorFrame = (id, anchorId) => hostedIn(id).find(key => HOSTED[key].anchor === anchorId) ?? id;

// Opening a cached section starts a refresh too, and a section that hosts others (usage shows llm-restore) draws its
// anchors from the hosted frames, which render on their own schedule, however long their loads take. So the landing is
// repeated whenever the section or a hosted one renders and the row it highlighted was replaced. Watching ends once the
// row is shown with none of them still loading (later polls must not scroll the page); once `frame`, the one that draws
// the row, has rendered without it (a definitive miss: a later row that happens to match is not scrolled to); when the
// reader opens a section; or when `frame` fails to load. Another frame's failure does not end it. A hosted frame can
// finish before its host has mounted it (on a cold start usage moves the restore frame inside itself when it renders):
// the row is then in `frame` but not yet in the section, which is no miss, so the watch waits for the host's render.
export function revealWhenRendered(id, anchorId, options, frame = anchorFrame(id, anchorId)) {
  stopRevealing();
  const within = scope => document.querySelector(`#${CSS.escape(scope)} [data-anchor="${CSS.escape(anchorId)}"]`);
  const find = () => within(id);
  const watched = [id, ...hostedIn(id)];
  let shown = null;
  let frameRendered = false;
  const land = () => { if (revealAnchor(id, anchorId, options)) shown = find(); };
  // A section's state is recorded just before its load is marked finished, so the one that just rendered counts as done.
  const settled = rendered => (shown?.isConnected
    ? watched.every(member => member === rendered || !isLoading(member))
    : frameRendered && !within(frame));
  land();
  if (settled(null)) return;
  const stop = onState((changed, entry) => {
    if (!watched.includes(changed)) return;
    if (currentSection() !== id || (changed === frame && entry.error)) {
      stop();
      return;
    }
    if (changed === frame) frameRendered = true;
    if (!shown?.isConnected) land();
    if (settled(changed)) stop();
  });
  stopRevealing = stop;
}

function openSection(id, anchorId, options, frame) {
  stopRevealing();
  known.filter(entry => !HOSTED[entry]).forEach(entry => { document.getElementById(entry).hidden = entry !== id; });
  document.querySelectorAll('#nav a').forEach(link => {
    if (link.dataset.nav === id) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  document.getElementById('current-title').textContent = sectionTitle(id);
  stored.set(STORAGE.section, id);
  setDrawer(false, false);
  document.querySelector('main').scrollTo({top: 0});
  afterOpen(id);
  if (anchorId) revealWhenRendered(id, anchorId, options, frame);
}

export function goTo(raw, anchorId, options) {
  const target = resolveSection(raw);
  if (!target) return;
  const id = target.id;
  anchorId ??= target.anchorId;
  if (location.hash === `#${id}`) {
    openSection(id, anchorId, options, target.frame);
    return;
  }
  pendingAnchor = {anchorId, options, frame: target.frame};
  location.hash = id;
}

function onHashChange() {
  const target = resolveSection(location.hash.slice(1));
  if (!target) return;
  const {anchorId = target.anchorId, options, frame = target.frame} = pendingAnchor ?? {};
  pendingAnchor = null;
  if (location.hash !== `#${target.id}`) history.replaceState(null, '', `#${target.id}`);
  openSection(target.id, anchorId, options, frame);
}

// ---- theme ----

function applyTheme(value) {
  if (value === 'light' || value === 'dark') document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
}

function setupTheme(select) {
  select.value = stored.get(STORAGE.theme, 'system');
  applyTheme(select.value);
  select.addEventListener('change', () => {
    stored.set(STORAGE.theme, select.value);
    applyTheme(select.value);
  });
}

export function setConnection(text, kind, extra) {
  const element = document.getElementById('connection');
  if (element.dataset.kind === kind && element.textContent.startsWith(text) && extra) return;
  element.dataset.kind = kind;
  element.className = kind;
  element.replaceChildren(text);
  if (extra) element.append(extra);
}

/** Builds nav and frames for the registered ids and opens the section named by the hash, else the last one. */
export function buildShell({ids, onOpen, onRefresh, fallback}) {
  known = ids;
  afterOpen = onOpen;
  const nav = document.getElementById('nav');
  GROUPS.forEach(group => {
    const members = group.sections.filter(id => ids.includes(id));
    if (members.length) nav.append(navGroup(group, members));
  });
  nav.addEventListener('keydown', moveFocus);
  const main = document.querySelector('main');
  ids.forEach(id => main.append(frame(id, onRefresh)));
  onState(updateDots);
  setupTheme(document.getElementById('theme'));
  menu().addEventListener('click', () => setDrawer(!drawerOpen(), true));
  document.getElementById('backdrop').addEventListener('click', () => setDrawer(false, true));
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && drawerOpen()) setDrawer(false, true); });
  NARROW.addEventListener('change', () => setDrawer(false, false));
  addEventListener('hashchange', onHashChange);
  const first = resolveSection(location.hash.slice(1)) ?? resolveSection(storedSection()) ?? {id: fallback};
  history.replaceState(null, '', `#${first.id}`);
  openSection(first.id, first.anchorId, undefined, first.frame);
}

/** A link that opens a section (and scrolls to one of its rows) through the same path as the nav. */
export function sectionLink(text, id, anchorId, options) {
  const link = node('a', text, 'section-link');
  link.href = `#${id}`;
  link.addEventListener('click', event => {
    event.preventDefault();
    goTo(id, anchorId, options);
  });
  return link;
}
