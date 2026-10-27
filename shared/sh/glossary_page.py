"""Render the glossary as one offline page, without trusting model-written HTML.

The initial cards are escaped on the Python side; embedded JSON also escapes
closing tags. Browser code clones these safe cards for flashcards rather than
interpreting definitions as HTML a second time.
"""

from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from glossary import Entry

INLINE = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)")


def inline_markdown(text: str) -> str:
    def replace(match):
        for group, tag in enumerate(("code", "strong", "em"), 1):
            if match.group(group) is not None:
                return f"<{tag}>{match.group(group)}</{tag}>"
        return match.group(0)
    return INLINE.sub(replace, html.escape(text))


def occurrence(entry: Entry) -> str:
    meta = html.escape(f"{entry['repo'] or '—'} · {entry['at'][:10]} · {entry['host']}")
    link = entry.get("link")
    if link and link.startswith("file://"):
        meta += f' · <a href="{html.escape(link, quote=True)}">lesson</a>'
    return (f'<div class="occurrence"><div class="definition">{inline_markdown(entry["definition"])}</div>'
            f'<p class="meta" title="{html.escape(entry["at"], quote=True)}">{meta}</p></div>')


def card(index: int, entries: list[Entry], known: bool) -> str:
    term = html.escape(entries[0]["term"])
    badge = '<span class="badge">known</span>' if known else ""
    older = ""
    if len(entries) > 1:
        older = (f'<details><summary>Older definitions ({len(entries) - 1})</summary>'
                 + "".join(occurrence(entry) for entry in entries[1:]) + '</details>')
    return (f'<article class="card" data-index="{index}"><h2>{term} {badge}</h2>'
            + occurrence(entries[0]) + older
            + '<div class="actions"><button type="button" data-copy="known">Copy +known</button>'
            + '<button type="button" data-copy="lex">Copy +lex</button></div></article>')


def render(entries: list[Entry], known_terms: list[str]) -> str:
    """Entries arrive newest first; preserve that order within each term."""
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry["term"].casefold(), []).append(entry)
    known = {term.casefold() for term in known_terms}
    groups = [{"term": occurrences[0]["term"], "known": key in known, "entries": occurrences}
              for key, occurrences in grouped.items()]
    cards = "".join(card(index, group["entries"], group["known"]) for index, group in enumerate(groups))
    data = json.dumps(groups, ensure_ascii=False).replace("</", "<\\/").replace("<!", "\\u003c!")
    return PAGE.replace("<!--CARDS-->", cards).replace("<!--DATA-->", data)


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Glossary</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #f8f9fb; --surface: #fff; --fg: #182332; --muted: #596678;
  --line: #d4dce6; --accent: #205cb5; --badge: #e4f1e9; --badge-fg: #22633d;
  --radius: 10px; --space: 16px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141a22; --surface: #1d2632; --fg: #e6edf5; --muted: #adbbcd;
    --line: #3a4b60; --accent: #9ac3ff; --badge: #234331; --badge-fg: #afe3c0;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui,sans-serif; }
main { max-width: 920px; margin: 0 auto; padding: 24px var(--space) 48px; }
h1 { margin: 0 0 var(--space); font-size: 1.5rem; }
h2 { margin: 0 0 12px; font-size: 1.15rem; line-height: 1.4; overflow-wrap: anywhere; }
.controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
label { display: grid; gap: 4px; }
.search { flex: 1 1 230px; }
input[type="search"],select,button {
  min-height: 44px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--surface); color: var(--fg); padding: 8px 12px; font: inherit;
}
input[type="search"] { width: 100%; min-width: 0; }
button { cursor: pointer; }
button:hover { border-color: var(--accent); }
button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
button:disabled { opacity: .5; cursor: default; }
input:focus-visible,button:focus-visible,select:focus-visible,a:focus-visible,summary:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 3px;
}
.toggle { display: flex; flex-direction: row; align-items: center; min-height: 44px; gap: 8px; }
input[type="checkbox"] { width: 18px; height: 18px; accent-color: var(--accent); }
#count,.meta,.empty { color: var(--muted); }
#count { margin: 12px 0; }
#cards { display: grid; gap: var(--space); }
.card,#flash { min-width: 0; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--surface); padding: var(--space); overflow-wrap: anywhere; }
.badge { display: inline-block; font-size: .75rem; font-weight: 500; border-radius: 4px;
  padding: 1px 6px; color: var(--badge-fg); background: var(--badge); vertical-align: middle; }
.definition { white-space: pre-wrap; overflow-wrap: anywhere; }
code { font-family: ui-monospace,monospace; font-size: .9em; overflow-wrap: anywhere; }
a { color: var(--accent); }
.meta { margin: 8px 0 0; font-size: .85rem; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: var(--space); }
.actions button { font-size: .9rem; }
details { border-top: 1px solid var(--line); margin-top: var(--space); padding-top: 12px; }
summary { cursor: pointer; color: var(--muted); }
details .occurrence { margin-top: var(--space); }
#flash-term { margin-top: 8px; font-size: 1.3rem; }
#flash-count { color: var(--muted); }
[hidden] { display: none !important; }
.clipboard-fallback { position: fixed; top: 0; left: 0; width: 1px; height: 1px; opacity: 0; }
@media (max-width: 480px) { .controls > button { flex: 1 1 auto; } }
</style>
</head>
<body>
<main>
<h1>Glossary</h1>
<div class="controls">
<label class="search">Search<input id="search" type="search" placeholder="Term, definition or repo" autocomplete="off"></label>
<label>Sort<select id="sort"><option value="newest">Newest</option><option value="az">A–Z</option></select></label>
<label class="toggle"><input id="hide-known" type="checkbox">Hide known</label>
<button id="view" type="button" aria-pressed="false">Flashcards</button>
</div>
<p id="count" role="status"></p>
<section id="cards" aria-label="Terms"><!--CARDS--></section>
<p id="empty" class="empty" hidden>No matching terms.</p>
<section id="flash" aria-label="Flashcards" hidden>
<div id="flash-count" role="status"></div>
<h2 id="flash-term"></h2>
<div id="flash-answer" hidden></div>
<div class="actions">
<button id="show" type="button">Show</button><button id="next" type="button">Next</button>
<button type="button" data-copy="known">Copy +known</button>
<button type="button" data-copy="lex">Copy +lex</button>
</div>
</section>
</main>
<script id="glossary-data" type="application/json"><!--DATA--></script>
<script>
'use strict';
const groups = JSON.parse(document.getElementById('glossary-data').textContent);
const byId = id => document.getElementById(id);
const cards = Array.from(document.querySelectorAll('#cards .card'));
const search = byId('search');
const sort = byId('sort');
const hideKnown = byId('hide-known');
const view = byId('view');
let flashMode = false;
let deck = [];
let position = 0;
let current = null;

function preferences() {
  try {
    const saved = JSON.parse(localStorage.getItem('glossary-controls') || '{}');
    sort.value = saved.sort === 'az' ? 'az' : 'newest';
    hideKnown.checked = saved.hideKnown === true;
  } catch (_) { /* Blocked browser storage is optional. */ }
}
function savePreferences() {
  try {
    localStorage.setItem('glossary-controls', JSON.stringify({sort: sort.value, hideKnown: hideKnown.checked}));
  } catch (_) { /* The page remains fully usable without storage. */ }
}
function queryFromHash() {
  search.value = new URLSearchParams(location.hash.slice(1)).get('q') || '';
}
function matches(group) {
  const haystack = [group.term, ...group.entries.map(entry => [entry.definition, entry.repo || ''].join(' '))];
  return haystack.join(' ').toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase());
}
function shuffled(indices) {
  const result = indices.slice();
  for (let i = result.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [result[i], result[j]] = [result[j], result[i]];
  }
  return result;
}
function showFlashcard() {
  current = deck.length ? deck[position] : null;
  byId('flash-count').textContent = deck.length ? `${position + 1} of ${deck.length}` : '0 of 0';
  byId('flash-term').textContent = current === null ? 'No non-known terms match.' : groups[current].term;
  const answer = byId('flash-answer');
  answer.replaceChildren();
  answer.hidden = true;
  if (current !== null) answer.append(cards[current].querySelector('.occurrence').cloneNode(true));
  for (const button of byId('flash').querySelectorAll('button')) button.disabled = current === null;
}
function update() {
  const indices = groups.map((_, index) => index).filter(index => matches(groups[index]));
  if (sort.value === 'az') indices.sort((a, b) => groups[a].term.localeCompare(groups[b].term));
  for (const card of cards) card.hidden = true;
  let visible = 0;
  for (const index of indices) {
    byId('cards').append(cards[index]);
    cards[index].hidden = hideKnown.checked && groups[index].known;
    if (!cards[index].hidden) visible++;
  }
  byId('cards').hidden = flashMode;
  byId('empty').hidden = flashMode || visible !== 0;
  byId('flash').hidden = !flashMode;
  byId('count').textContent = `${visible} of ${groups.length} terms`;
  view.setAttribute('aria-pressed', String(flashMode));
  view.textContent = flashMode ? 'List view' : 'Flashcards';
  deck = shuffled(indices.filter(index => !groups[index].known));
  position = 0;
  showFlashcard();
  savePreferences();
}
function fallbackCopy(text) {
  const previous = document.activeElement;
  const area = document.createElement('textarea');
  area.value = text;
  area.className = 'clipboard-fallback';
  document.body.append(area);
  try {
    area.select();
    if (!document.execCommand('copy')) throw new Error('Copy refused');
  } finally {
    area.remove();
    if (previous && previous.focus) previous.focus();
  }
}
async function copyCommand(button) {
  const card = button.closest('[data-index]');
  const index = card ? Number(card.dataset.index) : current;
  if (index === null) return;
  const command = `+${button.dataset.copy} ${groups[index].term}`;
  const label = `Copy +${button.dataset.copy}`;
  try {
    try {
      if (!navigator.clipboard) throw new Error('No clipboard API');
      await navigator.clipboard.writeText(command);
    } catch (_) { fallbackCopy(command); }
    button.textContent = 'Copied';
  } catch (_) { button.textContent = 'Copy unavailable'; }
  setTimeout(() => { button.textContent = label; }, 1400);
}
search.addEventListener('input', update);
sort.addEventListener('change', update);
hideKnown.addEventListener('change', update);
view.addEventListener('click', () => { flashMode = !flashMode; update(); });
byId('show').addEventListener('click', () => { byId('flash-answer').hidden = false; });
byId('next').addEventListener('click', () => {
  if (!deck.length) return;
  position++;
  if (position >= deck.length) { deck = shuffled(deck); position = 0; }
  showFlashcard();
});
document.addEventListener('click', event => {
  const button = event.target.closest('button[data-copy]');
  if (button) copyCommand(button);
});
window.addEventListener('hashchange', () => { queryFromHash(); update(); });
preferences();
queryFromHash();
update();
</script>
</body>
</html>
'''
