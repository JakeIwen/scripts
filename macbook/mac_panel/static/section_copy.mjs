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

// ---- per-section copy for Metered, Amphetamine, BTT guard, ntfy, Usage and Settings ----

export const METERED_COPY = {
  ssidsTitle: 'Metered SSIDs',
  ssidsInfo: 'Exact, case-sensitive Wi-Fi names that count as metered. Additions work offline and sync to the Pi later; removal needs the Pi and its current revision.',
  ssidsEmpty: 'No SSIDs are metered',
  lowDataTitle: 'Low Data Mode',
  lowDataInfo: 'Enable only after the sudoers / LAN Time Machine test passes. Wi-Fi is marked constrained while metered and cleared when unmetered or approved. A flag this control did not set (native per-network Low Data Mode) is left alone.',
  macConsumersTitle: 'Mac consumers',
  piConsumersTitle: 'Pi consumers',
  piConsumersInfo: 'Pi consumers are approved on the Pi with its own revision; this page only shows what the Pi reported.',
  approveInfo: 'Lets a gated consumer use a metered network for a limited time. An episode lasts until the current network connection ends.',
  modeTitle: 'Mac mode',
  modeInfo: 'Auto follows the SSID rules. Metered or Unmetered overrides them for the chosen time.',
  exemptInfo: 'An exempt consumer is never held back, whatever the verdict.',
  advancedTitle: 'Advanced',
  historyTitle: 'Network history',
  historyInfo: 'Whether the network was metered, sampled every minute while the panel runs. Unknown means the state was missing or stale. Gaps mean the panel was not running.',
  consumerHistoryTitle: 'Per-consumer history',
  noHistory: 'No history recorded for this range yet',
  stale: 'State is missing or stale',
};

export const AMPHETAMINE_COPY = {
  lockTitle: 'Automatic screen lock',
  lockInfo: 'Locks the screen after the idle time, but only during an active Amphetamine keep-awake session. Turning it on starts a session if none is running.',
  minutesLabel: 'Idle time',
  minutesInfo: 'Minutes without input before the screen locks. Applying restarts Amphetamine and keeps an indefinite open-display session.',
  sessionLabel: 'Keep-awake session',
  sessionInfo: 'Active: Amphetamine is keeping the Mac awake. Inactive: no session, so automatic lock cannot fire. Unavailable: the session could not be read. Indefinite and special sessions are preserved or refused, never replaced.',
  displayLabel: 'Display after locking',
  displayInfo: 'Whether the display may sleep once the screen is locked.',
  batteryLabel: 'Battery cutoff',
  batteryInfo: 'Read-only here: Amphetamine owns this preference and the panel cannot change it.',
  needsSession: 'Automatic locking needs an active Amphetamine session.',
};

export const GUARD_COPY = {
  statusTitle: 'Status',
  monitorInfo: 'The optional scheduled monitor audits BetterTouchTool and alerts on drift. Without it the audit button here is the only check.',
  auditInfo: 'Compares BetterTouchTool\'s triggers with the known-good checkpoint. Drift means something was added, removed or changed.',
  acceptanceInfo: 'The monitor accepts a change made while the same BTT process kept running, once it has stayed unchanged for 10 minutes. Changes after a BTT relaunch or update are alerted, never accepted.',
  checkpointInfo: 'The saved known-good copy the audit compares against.',
  actionsTitle: 'Actions',
  inspectInfo: 'Reads BTT and lists findings. Changes nothing.',
  auditActionInfo: 'Runs the integrity audit against the active checkpoint. Changes nothing.',
  checkpointActionInfo: 'Accepts the current BTT configuration as the new known-good checkpoint. Check the BTT menu yourself first. Repair is available only from the owner\'s Terminal.',
};

export const NTFY_COPY = {
  routesTitle: 'Alert routes',
  routesInfo: 'Each row is a hook that can send a phone alert: its source, the events that trigger it, the sender script and the destination. Topic URLs are secrets and never shown.',
  empty: 'No alert routes found',
  testLabel: 'Test alert',
  testInfo: 'Sends one real alert to the phone through the shared warning route.',
};

export const USAGE_COPY = {
  budgetsTitle: 'Allowance left',
  budgetsInfo: 'Percent left in each window. The line marks an even spend through the window: a fill short of it means spending is ahead of schedule.',
  routingTitle: 'Routing',
  routingInfo: 'The usage-budget script weighs the windows and picks which family does the work and which reviews it.',
  historyTitle: 'History',
  historyInfo: 'Allowance left over time, sampled every 15 minutes while the panel runs.',
  creditsInfo: 'Credits left against what the daily allowance would use before they expire. Short of full means they would run out early.',
  noData: 'The usage tool gave no snapshot',
  noHistory: 'No usage history recorded for this range yet',
  staleMinutes: 120,
};

export const SETTINGS_COPY = {
  empty: 'No tools declare settings',
  resetInfo: 'Restores every field of this tool to its declared default.',
};
