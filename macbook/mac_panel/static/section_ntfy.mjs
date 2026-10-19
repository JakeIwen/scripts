// ntfy alerts: routing metadata only; destinations stay hidden and the one action is a confirmed test.
import {button, node, notice, table} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {Level} from './status_cell.mjs';

const SECTION = 'ntfy';

// Sources sharing one sender, destination and configuration file read as one route.
function mergeAlerts(alerts) {
  const groups = new Map();
  alerts.forEach(alert => {
    const key = [alert.script, alert.destination, alert.configuration].join('\n');
    const group = groups.get(key) || {sources: [], events: [], script: alert.script,
      destination: alert.destination, configuration: alert.configuration};
    group.sources.push(alert.source);
    group.events.push({event: alert.event, matcher: alert.matcher});
    groups.set(key, group);
  });
  return [...groups.values()];
}

function eventsCell(events) {
  const cell = node('div');
  events.forEach(({event, matcher}) => {
    const line = node('div', event);
    if (matcher) line.append(' · ', node('code', matcher));
    cell.append(line);
  });
  return cell;
}

function senderCell(script) {
  if (!script) return node('span', '—', 'muted');
  const cell = node('code', script.split('/').pop());
  cell.title = script;
  return cell;
}

export function render(data) {
  const root = node('div');
  data.errors.forEach(error => root.append(node('p', error, 'error')));
  const rows = mergeAlerts(data.alerts).map(group => [group.sources.join(', '), eventsCell(group.events),
    senderCell(group.script), group.destination, node('code', group.configuration)]);
  root.append(table(['Source', 'Events', 'Sender', 'Destination', 'Configuration'], rows));
  root.append(button('Send a test alert (shared warning route)', () => action(SECTION, '/api/ntfy/test', {confirmed: true}, {
    after: result => notice(SECTION, result.sent ? 'Test alert sent to the phone' : 'Test alert was not sent', result.sent ? 'ok' : 'error'),
  }), {confirm: 'Confirm: send a phone alert', key: 'test'}));
  const summary = [`${data.alerts.length} routes`, data.errors.length && `${data.errors.length} unreadable`].filter(Boolean).join(' · ');
  return {node: root, summary, level: data.errors.length ? Level.WARN : Level.IDLE};
}
