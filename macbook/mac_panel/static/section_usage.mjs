// Placeholder until the history work supplies /api/usage; keeps the nav entry, search and tests complete.
import {emptyState, node} from './panel_dom.mjs';
import {Level} from './status_cell.mjs';

export function render() {
  const root = node('div');
  root.append(emptyState('Usage data arrives with the history work'));
  return {node: root, summary: 'not available yet', level: Level.IDLE};
}
