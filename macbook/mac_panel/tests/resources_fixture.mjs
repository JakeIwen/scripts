// One realistic /api/resources payload (the shape in the Mac Panel plan), plus helpers that read a rendered tree.
// The DOM stub's Element stands in for Node so `instanceof Node` checks in the page code hold.
import {Element} from './dom_fixture.mjs';

globalThis.Node = Element;
document.createElementNS ??= (_namespace, tag) => new Element(tag);
document.addEventListener ??= () => {};
document.documentElement ??= {};
globalThis.window ??= new EventTarget();
globalThis.getComputedStyle ??= () => ({getPropertyValue: () => '12px'});

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;
const proc = (pid, ppid, depth, name, cpu, rss, extra = {}) => ({pid, ppid, depth, name, cpu, rss, nice: 0, stopped: false,
  started: 'Tue Nov 17 06:00:00 2026', blocked: null, ...extra});

export const payload = () => ({
  sampled_at: '2026-11-17T08:00:00Z',
  health: {level: 'ok', reasons: [], unavailable: []},
  system: {cpu_percent: 37.5, ncpu: 10, load: [2.1, 1.8, 1.5], process_count: 612, uptime_seconds: 3 * 86400 + 5 * 3600,
    memory: {total: 32 * GIB, used: 18 * GIB, app: 9 * GIB, wired: 4 * GIB, compressed: 5 * GIB, cached: 8 * GIB, free: 5 * GIB,
      other: 1 * GIB, swap_used: 512 * MIB, swap_total: 4 * GIB, pressure: 'normal'},
    thermal: 'nominal', power: {source: 'ac', percent: 88, charging: true}, disk: {total: 994 * GIB, free: 412 * GIB}},
  totals: {claude: {cpu: 41.2, rss: 2.4 * GIB, count: 9}, codex: {cpu: 3.1, rss: 600 * MIB, count: 3},
    infra: {cpu: 5.0, rss: 900 * MIB, count: 4}, compute: {cpu: 0, rss: 120 * MIB, count: 2}},
  groups: [
    {id: 'claude:4100', kind: 'claude', name: 'panel work', name_source: 'registry', detail: 'interactive', status: 'busy', cwd: '/Users/jacobr/dev/scripts', parent: null,
      root_pid: 4100, started: 'Tue Nov 17 06:00:00 2026', cpu: 30.2, rss: 1.4 * GIB, count: 5, tree_blocked: 'Nested session "inner" is running inside it; end it first.',
      processes: [proc(4100, 1, 0, 'claude', 25.1, 900 * MIB), proc(4110, 4100, 1, 'zsh', 0, 8 * MIB), proc(4120, 4110, 2, 'node', 4.9, 300 * MIB),
        proc(4130, 4100, 1, 'claude', 0.2, 200 * MIB)]},
    {id: 'claude:4200', kind: 'claude', name: 'inner', name_source: 'registry', detail: 'interactive', status: 'idle', cwd: '/Users/jacobr/dev/scripts/pi', parent: 'claude:4100',
      root_pid: 4200, started: 'Tue Nov 17 07:00:00 2026', cpu: 11.0, rss: 600 * MIB, count: 2, tree_blocked: null,
      processes: [proc(4200, 4130, 0, 'claude', 11.0, 500 * MIB), proc(4210, 4200, 1, 'git', 0, 100 * MIB, {stopped: true})]},
    {id: 'claude:5000', kind: 'claude', name: 'agent-b', name_source: 'fallback', detail: 'headless', status: 'idle', cwd: '/tmp', parent: null,
      root_pid: 5000, started: 'Tue Nov 17 05:00:00 2026', cpu: 0.4, rss: 300 * MIB, count: 1, tree_blocked: null,
      processes: [proc(5000, 1, 0, 'claude', 0.4, 300 * MIB)]},
    {id: 'codex:6000', kind: 'codex', name: 'codex · scripts', detail: null, status: 'busy', cwd: '/Users/jacobr/dev/scripts', parent: null,
      root_pid: 6000, started: 'Tue Nov 17 07:30:00 2026', cpu: 3.1, rss: 600 * MIB, count: 3, tree_blocked: null,
      processes: [proc(6000, 1, 0, 'codex', 3.1, 400 * MIB), proc(6010, 6000, 1, 'bash', 0, 6 * MIB), proc(6020, 6010, 2, 'rg', 0, 12 * MIB)]},
    {id: 'infra:700', kind: 'infra', name: 'model gateway', detail: 'node model-gateway.js', status: null, cwd: null, parent: null,
      root_pid: 700, started: 'Mon Nov 16 09:00:00 2026', cpu: 2.0, rss: 400 * MIB, count: 1, tree_blocked: 'Whole-tree actions exist only for Claude and Codex sessions',
      processes: [proc(700, 1, 0, 'node', 2.0, 400 * MIB, {blocked: 'Shared by every session; restart it from Services.'})]},
    {id: 'compute:800', kind: 'compute', name: 'worker', detail: 'Python -m van_compute.worker', status: null, cwd: null, parent: null,
      root_pid: 800, started: 'Mon Nov 16 09:00:00 2026', cpu: 0, rss: 120 * MIB, count: 2, tree_blocked: 'Whole-tree actions exist only for Claude and Codex sessions',
      processes: [proc(800, 1, 0, 'python3', 0, 100 * MIB, {blocked: 'Owned by van-compute; killing a job here would skip its cleanup.'})]},
  ],
  apps: [
    {name: 'Safari', root_pid: 900, started: 'Tue Nov 17 04:00:00 2026', cpu: 12.5, rss: 1.8 * GIB, count: 14},
    {name: 'Slack', root_pid: 910, started: 'Tue Nov 17 04:10:00 2026', cpu: 3.0, rss: 1.1 * GIB, count: 8},
  ],
  processes: [
    {pid: 901, ppid: 900, user: 'jacobr', name: 'Safari', cpu: 12.5, rss: 900 * MIB, nice: 0, stopped: false, started: 'Tue Nov 17 04:00:00 2026', group: null, blocked: null},
    {pid: 4120, ppid: 4110, user: 'jacobr', name: 'node', cpu: 4.9, rss: 300 * MIB, nice: 10, stopped: false, started: 'Tue Nov 17 06:00:00 2026', group: 'claude:4100', blocked: null},
    {pid: 4210, ppid: 4200, user: 'jacobr', name: 'git', cpu: 0, rss: 100 * MIB, nice: 0, stopped: true, started: 'Tue Nov 17 07:00:00 2026', group: 'claude:4200', blocked: null},
    {pid: 1200, ppid: 1, user: 'root', name: 'WindowServer', cpu: 6.0, rss: 250 * MIB, nice: 0, stopped: false, started: 'Mon Nov 16 09:00:00 2026', group: null,
      blocked: 'Not owned by you.'},
    {pid: 700, ppid: 1, user: 'jacobr', name: 'node', cpu: 2.0, rss: 400 * MIB, nice: 0, stopped: false, started: 'Mon Nov 16 09:00:00 2026', group: 'infra:700',
      blocked: 'Shared by every session; restart it from Services.'},
  ],
});

// ---- reading a rendered tree (the stub has no querySelectorAll) ----

export function walk(root, visit) {
  if (root === null || typeof root !== 'object') return;
  visit(root);
  (root.childNodes ?? []).forEach(child => walk(child, visit));
}

export function findAll(root, test) {
  const found = [];
  walk(root, element => { if (test(element)) found.push(element); });
  return found;
}

export const hasClass = (element, name) => String(element.className ?? '').split(' ').includes(name);
export const buttonsIn = root => findAll(root, element => element.tagName === 'BUTTON' && !hasClass(element, 'info-button')
  && !hasClass(element, 'range-choice') && !hasClass(element, 'refresh-one'));
export const labelsIn = root => buttonsIn(root).map(button => button.textContent);
export const withKey = (root, key) => findAll(root, element => element.dataset?.key === key);

// Armed buttons are keyed by `<prefix>-<pid>-<start token>`; a test names the row by its pid.
export const withIdentity = (root, prefix, pid) => findAll(root, element => String(element.dataset?.key ?? '').startsWith(`${prefix}-${pid}-`));
