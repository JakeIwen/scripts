// Automations expose each declared tool's status and its explicit panel commands.
import {button, card, confirmText, emptyState, info, kv, node, timeNode} from './panel_dom.mjs';
import {action} from './panel_sections.mjs';
import {stageAction} from './search_actions.mjs';
import {JOBS_COPY} from './section_copy.mjs';
import {Level, status, worst} from './status_cell.mjs';
import {latestTask, resumeTasks, taskLine, watchTask} from './task_receipt.mjs';

const SECTION = 'automations';
const COPY = JOBS_COPY.automations;
const automationAnchor = automation => `automation-${automation.tool}`;
const actionKey = (automation, item) => `${automation.tool}-${item.name}`;
const taskJob = (automation, item) => `automation:${automation.tool}:${item.name}`;

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

// A synchronous action answers with its message; an async one answers with a task receipt that is followed to its end.
function actionsFor(automation) {
  const result = node('div', null, 'actions');
  automation.actions.forEach(item => {
    const body = {tool: automation.tool, action: item.name};
    if (item.confirm === true) body.confirmed = true;
    const after = receipt => { if (item.async) watchTask(SECTION, receipt, `${automation.title}: ${item.label}`); };
    result.append(button(item.label, () => action(SECTION, '/api/automations', body, {after}), {
      confirm: item.confirm === true ? confirmText(item.label.toLowerCase()) : undefined,
      key: actionKey(automation, item), title: item.description,
    }));
    if (item.async) result.append(info(COPY.asyncInfo));
  });
  return result;
}

function automationCard(automation, tasks) {
  const level = levelFor(automation);
  const summary = automation.status?.summary ?? 'unavailable';
  const body = node('div');
  if (automation.description) body.append(node('p', automation.description, 'muted'));
  const summaryLine = node('p', null, 'summary-line');
  summaryLine.append(status(level, summary));
  body.append(summaryLine, kv((automation.status?.fields ?? []).map(field => [field.label, fieldValue(field)])));
  if (automation.error) body.append(node('p', automation.error, 'error'));
  automation.actions.forEach(item => {
    const task = latestTask(tasks, taskJob(automation, item));
    if (task) body.append(taskLine(task));
  });
  const links = linksFor(automation);
  if (links.childElementCount) body.append(links);
  if (automation.actions.length) body.append(actionsFor(automation));
  return {node: card({title: automation.title, level, body, anchor: automationAnchor(automation)}), level, summary: `${automation.title}: ${summary}`};
}

export function render(data) {
  const root = node('div');
  if (!data.automations.length) {
    root.append(emptyState(COPY.empty));
    return {node: root, summary: 'no automations', level: Level.IDLE};
  }
  const rendered = data.automations.map(automation => automationCard(automation, data.tasks));
  rendered.forEach(item => root.append(item.node));
  const labels = new Map(data.automations.flatMap(automation => automation.actions.map(item =>
    [taskJob(automation, item), `${automation.title}: ${item.label}`])));
  resumeTasks(SECTION, data.tasks, task => labels.get(task.job) ?? task.job);
  return {
    node: root,
    summary: rendered.map(item => item.summary).join(' · '),
    level: worst(rendered.map(item => item.level)),
  };
}

export function searchItems(data) {
  return data.automations.map(automation => ({
    title: automation.title, subtitle: automation.status?.summary ?? 'unavailable', keywords: ['automation', automation.tool],
    anchor: automationAnchor(automation),
    actions: automation.actions.map(item => ({
      label: item.label,
      run: () => stageAction(SECTION, automationAnchor(automation), actionKey(automation, item), {arm: item.confirm === true}),
    })),
  }));
}

export function attentionItems(data) {
  return data.automations.map(automation => ({automation, level: levelFor(automation)}))
    .filter(({level}) => level === Level.WARN || level === Level.ERROR)
    .map(({automation, level}) => ({
      level, text: `${automation.title}: ${automation.status?.summary ?? automation.error ?? 'unavailable'}`,
      anchor: automationAnchor(automation),
    }));
}
