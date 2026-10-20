// One vocabulary for "how is this thing doing", shared by every section and the tab dots.
import {node} from './panel_dom.mjs';

export const Level = Object.freeze({OK: 'ok', IDLE: 'idle', UNKNOWN: 'unknown', WARN: 'warn', ERROR: 'error'});
const RANK = [Level.OK, Level.IDLE, Level.UNKNOWN, Level.WARN, Level.ERROR];

export function worst(levels) {
  return levels.reduce((current, level) => RANK.indexOf(level) > RANK.indexOf(current) ? level : current, Level.OK);
}

export function status(level, text, detail) {
  const result = node('span', null, `status level-${level}`);
  result.append(node('span', null, `dot level-${level}`), node('span', text, 'text'));
  if (detail) {
    const extra = node('span', null, 'detail');
    extra.append(detail instanceof Node ? detail : String(detail));
    result.append(extra);
  }
  return result;
}

export function tag(text, level) {
  return node('span', text, level ? `tag level-${level}` : 'tag');
}
