// Automations expose each declared tool's status and its explicit panel commands.
import {button, keyValues, node, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {Level, status, worst} from './status_cell.mjs';

const SECTION = 'automations';

function levelFor(automation) {
  if (automation.status) return automation.status.level ?? Level.UNKNOWN;
  return !automation.description && !automation.actions.length ? Level.ERROR : Level.WARN;
}

function fieldValue(field) {
  const hasValue = field.value !== null && field.value !== undefined;
  const hasTime = field.time !== null && field.time !== undefined;
  const time = hasTime ? timeNode(field.time) : null;
  if (field.level) {
    if (!time) return status(field.level, field.value ?? '');
    const detail = node('span');
    detail.append(' · ', time);
    return status(field.level, field.value ?? '', detail);
  }
  const result = node('span');
  if (hasValue) result.append(String(field.value));
  if (time) result.append(hasValue ? ' · ' : '', time);
  return result;
}

function link(label, target, key) {
  const result = node('a', label);
  result.href = target;
  result.title = label;
  result.dataset.key = key;
  return result;
}

function linksFor(automation) {
  const result = node('div', null, 'actions');
  if (automation.service) result.append(link('Service', '#services', `${automation.tool}-service`));
  if (automation.settings) result.append(link('Settings', '#settings', `${automation.tool}-settings`));
  return result;
}

function actionsFor(automation) {
  const result = node('div', null, 'actions');
  automation.actions.forEach(item => {
    const confirmed = item.confirm === true;
    const body = {tool: automation.tool, action: item.name};
    if (confirmed) body.confirmed = true;
    result.append(button(item.label, () => action(SECTION, '/api/automations', body), {
      confirm: confirmed ? `Confirm ${item.label.toLowerCase()}` : undefined,
      key: `${automation.tool}-${item.name}`,
      title: item.description,
    }));
  });
  return result;
}

function automationNode(automation) {
  const root = node('div', null, 'tool');
  const level = levelFor(automation);
  const summary = automation.status?.summary ?? 'unavailable';
  root.append(node('h3', automation.title), node('p', automation.description, 'muted'));
  const summaryLine = node('p', null, 'summary-line');
  summaryLine.append(status(level, summary));
  root.append(summaryLine);
  root.append(keyValues((automation.status?.fields ?? []).map(field => [field.label, fieldValue(field)])));
  if (automation.error) root.append(node('p', automation.error, 'error'));
  const links = linksFor(automation);
  if (links.childElementCount) root.append(links);
  if (automation.actions.length) root.append(actionsFor(automation));
  return {root, level, summary: `${automation.title}: ${summary}`};
}

export function render(data) {
  const automations = data.automations;
  const root = node('div');
  if (!automations.length) {
    root.append(node('p', 'No automations declared'));
    return {node: root, summary: 'no automations', level: Level.IDLE};
  }
  const rendered = automations.map(automationNode);
  rendered.forEach(item => root.append(item.root));
  return {
    node: root,
    summary: rendered.map(item => item.summary).join(' · '),
    level: worst(rendered.map(item => item.level)),
  };
}
