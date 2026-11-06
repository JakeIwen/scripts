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
    scheduleEmpty: 'No jobs are queued.',
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
