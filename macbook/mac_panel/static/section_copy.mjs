// Navigation structure and the one-paragraph explanation behind each section's info icon.
export const DEFAULT_SECTION = 'overview';

// Order is the sidebar's order. A one-section group shows no header of its own.
export const GROUPS = [
  {id: 'home', title: 'Overview', sections: ['overview']},
  {id: 'jobs', title: 'Jobs & services', sections: ['services', 'deferred', 'crontab', 'automations', 'llm-restore']},
  {id: 'network', title: 'Network', sections: ['metered']},
  {id: 'mac', title: 'Mac', sections: ['amphetamine', 'guard', 'ntfy']},
  {id: 'usage', title: 'LLM', sections: ['usage']},
  {id: 'settings', title: 'Settings', sections: ['settings']},
];

export const SECTION_COPY = {
  overview: {
    title: 'Overview',
    info: 'Everything that needs attention across the panel, the controls you reach for most, and a card for every section. It reads the data each section already loaded; Refresh all reloads them.',
  },
  services: {
    title: 'Services',
    info: 'The launchd jobs under com.jacobr.* and the system daemons installed for them. Each card shows its state, a health timeline and log tails. Start, stop and restart ask for confirmation. A system daemon can only be restarted, and only once the sudoers rule is installed.',
  },
  deferred: {
    title: 'Deferred jobs',
    info: 'One-off future jobs queued with deferred.zsh. A checker runs hourly and at login, runs each job when due, and alerts on failure. Add a job here, or run, retry, reschedule and cancel one; all of it goes through that CLI.',
  },
  crontab: {
    title: 'Crontab',
    info: 'The tracked macbook/crontab compared with the live one, with a timeline of the next runs. Run a job now, pause or resume it, or install the tracked file when no live-only lines would be lost.',
  },
  automations: {
    title: 'Automations',
    info: 'Background tools that declare themselves under shared/automations/. Each card shows the tool\'s own status and the actions it exposes; a slow action runs in the background and reports here when it finishes. The tool keeps its own state.',
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
    title: 'LLM',
    info: 'Claude and Codex allowance left in the current windows against an even spend line, the routing the usage-budget script chose from them, and that script\'s settings.',
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

// The one tool whose settings card lives in the LLM section (internal id `usage`) instead of Settings.
export const USAGE_SETTINGS_TOOL = 'usage-budget';

// ---- per-section copy for Metered, Amphetamine, BTT guard, ntfy, LLM and Settings ----

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
  historyInfo: 'Allowance left over time, sampled every 15 minutes while the panel runs. The dashed line is Claude\'s even-spend line for the week: a line below it means spending is ahead of schedule. Hover or tap for the values at one moment. Nothing is drawn before the panel started recording.',
  historyAllowanceTitle: 'Allowance left',
  historyAllowanceUnit: '% left',
  historyCreditsTitle: 'Credits left',
  historyCreditsUnit: 'credits',
  historySeries: {
    claude_weekly_left: 'Claude week',
    claude_session_left: 'Claude five-hour',
    codex_weekly_left: 'Codex week',
    claude_line_left: 'Claude even spend',
    credits: 'Credits',
  },
  creditsInfo: 'Credits left against what the daily allowance would use before they expire. Short of full means they would run out early.',
  noData: 'The usage tool gave no snapshot',
  noHistory: 'No usage history recorded for this range yet',
  settingsAnchor: 'budget-settings',
  settingsTitle: 'Usage budget',
  settingsUnavailable: 'The usage budget settings could not be loaded',
  staleMinutes: 120,
};

// The usage-budget script's modes, in the order the picker lists them; `kind` is whether the script can pick
// the mode itself (auto) or only a person can (manual). `auto` is the choice that clears a manual mode.
export const MODE_COPY = {
  title: 'Mode',
  info: 'The delegation mode the usage-budget script picked from the spend lines. Choosing one here overrides that automatic choice until it expires (the targeted provider\'s next weekly reset); Automatic clears the override. There is no duration to set.',
  automatic: {label: 'Automatic', text: 'Clear any manual mode; the script picks from the spend lines again.'},
  modes: {
    'claude-conserve': {kind: 'auto', text: 'Claude in deficit, GPT in surplus. GPT builds at the cheapest fit, Opus reviews its work, new sessions start on Astra.'},
    'gpt-conserve': {kind: 'auto', text: 'GPT in deficit, Claude in surplus. Claude builds at the cheapest fit, Astra reviews, new sessions start on Opus.'},
    'balanced-conserve': {kind: 'auto', text: 'Both in deficit. The family less deep in deficit builds, required reviews only, its frontier model parents.'},
    'balanced-burn': {kind: 'auto', text: 'Both in surplus. Either family builds by fit, step-ups are free on T2 and T3, optional reviews are added, either parent.'},
    'gpt-burn': {kind: 'manual', text: 'GPT builds everything, parents included, Opus still reviews, extra GPT reviews are added, sessions start on Astra.'},
    'claude-burn': {kind: 'manual', text: 'Claude builds everything, parents included, Astra still reviews, extra Opus reviews are added, sessions start on Fable or Opus.'},
  },
  apply: 'Apply',
  confirm: mode => `set mode ${mode}`,
  searchTitle: 'Set LLM mode',
  searchInfo: 'Pick a delegation mode, or clear it back to automatic',
};

export const SETTINGS_COPY = {
  empty: 'No tools declare settings',
  resetInfo: 'Restores every field of this tool to its declared default.',
  usagePointer: 'Usage budget settings live in LLM',
};

// ---- Jobs & services: strings the section renderers use, kept out of the render functions ----
export const JOBS_COPY = {
  services: {
    historyInfo: 'Each strip is one service\'s state over time, sampled once a minute while the panel runs. Taller bands are worse states; empty stretches mean the panel was not recording.',
    verbs: {restart: 'Restart', start: 'Start', stop: 'Stop'},
    verbHelp: {
      restart: 'Kill the job and launch it again now.',
      start: 'Enable the job and load it into launchd.',
      stop: 'Disable the job and unload it, so KeepAlive cannot bring it back.',
    },
    blocked: {restart: 'Not loaded, so there is nothing to restart. Use Start.', start: 'Already running.', stop: 'Not loaded.'},
    sudoMissing: 'Restarting a system daemon needs root. Install the sudoers rule once from Terminal with the command below, then reload this page.',
    sudoInstall: 'Install the rule:',
    statusOnly: 'status only',
    empty: 'No com.jacobr.* launchd jobs found.',
  },
  crontab: {
    scheduleInfo: 'Each tick is one scheduled run in the next window, read from the live crontab; hollow ticks have not happened yet and the amber line is now. Paused jobs have no runs. Jobs that run too often to draw are listed below the strip.',
    frequent: 'Not drawn, too frequent: ',
    nothingScheduled: 'No runs are scheduled in this window.',
    lastLogInfo: 'The modification time of the log file the job redirects its output to. It shows the job wrote something recently, not that it succeeded.',
    nextInfo: 'Computed from the schedule in this Mac\'s local time; a paused job has no next run.',
    runInfo: 'Runs the command once now with the cron environment, as cron would. Its output appears under the job when it finishes.',
    empty: 'No cron jobs.',
  },
  deferred: {
    scheduleInfo: 'Each tick is a queued job at its due time; hollow ticks are still to come and the amber line is now. A job past due runs when the hourly checker next looks.',
    scheduleEmpty: 'No jobs are due in this window.',
    addTitle: 'Add a job',
    addInfo: 'Queues a one-off job. The script runs once at the due time with the timeout you set; the checker alerts if it fails.',
    fields: {
      due: ['Due', 'Local date and time the job becomes due.'],
      name: ['Name', 'Lower-case letters, digits and hyphens, up to 60 characters. It names the job in lists and alerts.'],
      description: ['Description', 'Optional note shown with the job.'],
      script: ['Script', 'The whole script, starting with a #! line. It is saved with owner-only permissions and run by the checker.'],
      attempts: ['Attempts', 'How many times the checker tries before marking the job failed. Leave empty for the CLI default.'],
      timeout: ['Timeout', 'Seconds a single attempt may run. Leave empty for the CLI default.'],
    },
    defaultScript: '#!/bin/zsh\nset -eu\n\n',
    empty: 'No deferred jobs.',
  },
  automations: {
    empty: 'No automations declared.',
    asyncInfo: 'Runs in the background; the result appears here when it finishes, and the card refreshes.',
  },
  restore: {
    toggleInfo: 'Pauses or resumes the every-minute snapshot. The CLI owns its crontab block, so this changes nothing else.',
    sessionsInfo: 'Claude Code and Codex sessions detected as open at the last snapshot.',
    lastInfo: 'When the snapshot last ran. It should be within a minute while snapshots are on.',
  },
  overview: {
    quickTitle: 'Quick controls',
    lowDataInfo: 'Constrains Wi-Fi while the network is metered and clears it when unmetered or approved. Turn it on only after the sudoers / LAN Time Machine test in Metered has passed.',
    restoreInfo: 'The every-minute snapshot of open Claude Code and Codex sessions that llmrestore reopens after a reboot.',
    allSettings: 'All settings',
  },
};
