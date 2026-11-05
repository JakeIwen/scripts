// Navigation structure and the one-paragraph explanation behind each section's info icon.
export const DEFAULT_SECTION = 'overview';

// Order is the sidebar's order. A one-section group shows no header of its own.
export const GROUPS = [
  {id: 'home', title: 'Overview', sections: ['overview']},
  {id: 'jobs', title: 'Jobs & services', sections: ['services', 'deferred', 'crontab', 'automations', 'llm-restore']},
  {id: 'network', title: 'Network', sections: ['metered']},
  {id: 'mac', title: 'Mac', sections: ['amphetamine', 'guard', 'ntfy']},
  {id: 'usage', title: 'Usage', sections: ['usage']},
  {id: 'settings', title: 'Settings', sections: ['settings']},
];

export const SECTION_COPY = {
  overview: {
    title: 'Overview',
    info: 'Everything that needs attention across the panel, the controls you reach for most, and a card for every section. It reads the data each section already loaded; Refresh all reloads them.',
  },
  services: {
    title: 'Services',
    info: 'The launchd jobs under com.jacobr.* and the system daemons installed for them. A dot shows running, idle, failed or disabled; open a row for its log tails. Restart asks for confirmation. System daemons are status-only because restarting them needs root.',
  },
  deferred: {
    title: 'Deferred jobs',
    info: 'One-off future jobs queued with deferred.zsh. A checker runs hourly and at login, runs each job when due, and alerts on failure. Run now, retry, reschedule and cancel go through that CLI.',
  },
  crontab: {
    title: 'Crontab',
    info: 'The tracked macbook/crontab compared with the live one. Pause or resume a job, or install the tracked file when no live-only lines would be lost. Last log write is a hint from the job\'s redirected log, not proof it succeeded.',
  },
  automations: {
    title: 'Automations',
    info: 'Background tools that declare themselves under shared/automations/. Each card shows the tool\'s own status and the actions it exposes; the tool keeps its own state.',
  },
  'llm-restore': {
    title: 'llm-restore',
    info: 'Every minute a snapshot records the open Claude Code and Codex sessions so llmrestore can reopen them after a reboot. The switch pauses and resumes the snapshot; the CLI owns its crontab block.',
  },
  metered: {
    title: 'Metered',
    info: 'Whether the current Wi-Fi counts as metered, decided by exact SSID. Metered networks hold back big downloads and uploads for the consumers listed here. Pi consumers are approved on the Pi; this page only shows them.',
  },
  amphetamine: {
    title: 'Amphetamine',
    info: 'Amphetamine\'s automatic screen lock, which works only during an active keep-awake session. Applying restarts Amphetamine under a temporary caffeinate assertion and keeps an open-display session\'s choices.',
  },
  guard: {
    title: 'BTT guard',
    info: 'Integrity checks on BetterTouchTool\'s triggers against a known-good checkpoint. Audit is read-only; accepting a checkpoint is explicit. Repair is Terminal-only by design.',
  },
  ntfy: {
    title: 'ntfy alerts',
    info: 'Which Claude, Codex and panel hooks send phone alerts, read from their configuration. Topic URLs are secrets and never shown. The test button sends one real alert.',
  },
  usage: {
    title: 'Usage',
    info: 'Claude and Codex allowance left in the current windows against an even spend line, with the routing the usage-budget script chose from them.',
  },
  settings: {
    title: 'Settings',
    info: 'Settings for tools that declare them. Each save is checked against the values it was loaded from, so a conflicting edit asks you to reload. Reset restores the declared defaults.',
  },
};

export const sectionTitle = id => SECTION_COPY[id]?.title ?? id;

export function groupOf(id) {
  return GROUPS.find(group => group.sections.includes(id));
}
