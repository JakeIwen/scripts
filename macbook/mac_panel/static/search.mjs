// Search index and scoring, pure so Node can test them. The popover that shows the results is search_ui.mjs.
import {USAGE_SETTINGS_TOOL} from './section_copy.mjs';
import {settingAnchor, settingLabel} from './settings_fields.mjs';

export const MAX_RESULTS = 12;
// Title outranks subtitle outranks keywords.
const FIELD_WEIGHT = {title: 3, subtitle: 2, keywords: 1};
const MATCH = {word: 100, prefix: 70, substring: 40, subsequence: 15};
// Shorter words match too much unrelated text as a subsequence ("dim" in half the settings).
const SUBSEQUENCE_MIN_LENGTH = 4;
// Sections sort ahead of equally scored rows so "services" opens the section before one of its jobs.
const KIND_ORDER = {section: 0, group: 1, action: 2, item: 3, setting: 4};

export const words = text => String(text ?? '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

function isSubsequence(term, text) {
  let at = 0;
  for (const char of text) if (char === term[at] && ++at === term.length) return true;
  return false;
}

/** How well one query word matches a text: exact word > prefix > substring > subsequence, 0 for none. */
export function scoreTerm(term, text) {
  if (!text) return 0;
  const lower = String(text).toLowerCase();
  const parts = words(lower);
  if (parts.includes(term)) return MATCH.word;
  if (parts.some(part => part.startsWith(term))) return MATCH.prefix;
  if (lower.includes(term)) return MATCH.substring;
  if (term.length >= SUBSEQUENCE_MIN_LENGTH && isSubsequence(term, lower)) return MATCH.subsequence;
  return 0;
}

/** Sum over query words of the best weighted field match; 0 when any word matches nothing. */
export function scoreItem(terms, item) {
  const fields = {title: item.title, subtitle: item.subtitle, keywords: (item.keywords ?? []).join(' ')};
  let total = 0;
  for (const term of terms) {
    const best = Math.max(...Object.entries(FIELD_WEIGHT).map(([field, weight]) => weight * scoreTerm(term, fields[field])));
    if (best === 0) return 0;
    total += best;
  }
  return total;
}

export function search(items, query, limit = MAX_RESULTS) {
  const terms = words(query);
  if (!terms.length) return [];
  return items
    .map(item => ({item, score: scoreItem(terms, item)}))
    .filter(result => result.score > 0)
    .sort((a, b) => b.score - a.score
      || (KIND_ORDER[a.item.kind] ?? 9) - (KIND_ORDER[b.item.kind] ?? 9)
      || a.item.title.length - b.item.title.length
      || a.item.title.localeCompare(b.item.title))
    .slice(0, limit)
    .map(result => result.item);
}

// ---- index ----

export function settingItems(payload) {
  return (payload?.tools ?? []).filter(tool => !tool.error && tool.declaration).flatMap(({declaration}) =>
    declaration.settings.map(setting => ({
      id: `setting:${declaration.tool}:${setting.name}`,
      kind: 'setting',
      title: `${declaration.title}: ${settingLabel(setting)}`,
      subtitle: setting.description,
      keywords: [declaration.tool, setting.name],
      section: declaration.tool === USAGE_SETTINGS_TOOL ? 'usage' : 'settings',
      anchor: settingAnchor(declaration.tool, setting.name),
    })));
}

function staticItems({sections, groups}) {
  const titles = new Map(sections.map(entry => [entry.id, entry.title]));
  const sectionRows = sections.map(entry => ({
    id: `section:${entry.id}`, kind: 'section', title: entry.title, subtitle: entry.group, keywords: [entry.id], section: entry.id,
  }));
  // A one-section group is its own section; listing it twice would only duplicate the result.
  const groupRows = groups.filter(group => group.sections.length > 1).map(group => ({
    id: `group:${group.id}`, kind: 'group', title: group.title,
    subtitle: group.sections.map(id => titles.get(id)).join(', '), keywords: group.sections, section: group.sections[0],
  }));
  return [...sectionRows, ...groupRows];
}

// A section's own rows come from its optional searchItems(payload). One that throws loses only its rows.
function sectionRows(sections, payloadOf) {
  return sections.flatMap(entry => {
    const payload = payloadOf(entry.id);
    if (!entry.searchItems || payload === null || payload === undefined) return [];
    try {
      return entry.searchItems(payload).map((item, index) => ({
        kind: 'item', id: `${entry.id}:${index}:${item.title}`, ...item, section: item.section ?? entry.id,
        keywords: [...(item.keywords ?? []), ...(item.actions ?? []).map(action => action.label)],
      }));
    } catch {
      return [];
    }
  });
}

/**
 * sections: [{id, title, group, searchItems?}]; groups: [{id, title, sections}];
 * payloadOf(id): the latest payload the state registry holds for that section.
 */
export function buildIndex({sections, groups, payloadOf}) {
  // The LLM section carries its own copy of its tool's declaration, so those fields stay searchable before Settings loads.
  const fields = [...settingItems(payloadOf('settings')), ...settingItems(payloadOf('usage')?.budget_settings)];
  const unique = [...new Map(fields.map(item => [item.id, item])).values()];
  return [...staticItems({sections, groups}), ...unique, ...sectionRows(sections, payloadOf)];
}
