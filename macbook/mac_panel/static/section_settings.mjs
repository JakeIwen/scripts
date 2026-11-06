// Settings forms generated from each tool's declaration; saves go through tool_settings on the server.
import {card, emptyState, fieldError, node} from './panel_dom.mjs';
import {SETTINGS_COPY as COPY, USAGE_COPY, USAGE_SETTINGS_TOOL} from './section_copy.mjs';
import {toolCard} from './settings_card.mjs';
import {sectionLink} from './shell.mjs';
import {Level, worst} from './status_cell.mjs';

const SECTION = 'settings';

// A declaration the server could not load gets a card like a broken automation's: no form, just why.
function errorCard(tool) {
  return card({title: tool.tool, level: Level.ERROR, summary: 'unavailable', body: fieldError(tool.error)});
}

function rendered(tool) {
  if (tool.error) return {root: errorCard(tool), level: Level.ERROR, summary: `${tool.tool}: unavailable`};
  const level = tool.state.error ? Level.WARN : Level.IDLE;
  return {root: toolCard(tool, SECTION), level, summary: tool.declaration.title};
}

// The moved tool keeps a one-line pointer so its absence here is explained.
function pointerCard() {
  const link = sectionLink(COPY.usagePointer, 'usage', USAGE_COPY.settingsAnchor);
  return {root: card({title: link}), level: Level.IDLE, summary: ''};
}

export function render(data) {
  const root = node('div');
  const tools = data.tools.filter(tool => tool.tool !== USAGE_SETTINGS_TOOL).map(rendered);
  if (data.tools.some(tool => tool.tool === USAGE_SETTINGS_TOOL)) tools.push(pointerCard());
  tools.forEach(tool => root.append(tool.root));
  if (!tools.length) root.append(emptyState(COPY.empty));
  const summary = tools.map(tool => tool.summary).filter(Boolean).join(' · ') || 'none';
  return {node: root, summary, level: tools.length ? worst(tools.map(tool => tool.level)) : Level.IDLE};
}
