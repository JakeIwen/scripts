// Settings forms generated from each tool's declaration; saves go through tool_settings on the server.
import {card, emptyState, fieldError, node} from './panel_dom.mjs';
import {SETTINGS_COPY as COPY} from './section_copy.mjs';
import {toolCard} from './settings_card.mjs';
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

export function render(data) {
  const root = node('div');
  const tools = data.tools.map(rendered);
  tools.forEach(tool => root.append(tool.root));
  if (!tools.length) root.append(emptyState(COPY.empty));
  const summary = tools.map(tool => tool.summary).join(' · ') || 'none';
  return {node: root, summary, level: tools.length ? worst(tools.map(tool => tool.level)) : Level.IDLE};
}
