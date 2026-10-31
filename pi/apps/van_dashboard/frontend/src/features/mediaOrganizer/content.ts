import { AliasState, Classifier, ItemState, SkipReason, Stage } from './schema';

export const organizerCopy = {
  showRequired: 'Enter a show folder before saving this review.',
  unknownOutcome:
    'Operation outcome unknown: the dashboard no longer reports the accepted job. Check run history and qBittorrent before retrying. Nothing was replayed.',
  viewItems: 'View items',
  hideItems: 'Hide items',
  loadingItems: 'Loading run items…',
  itemsError: 'Could not load this run’s items. Refresh to try reading them again.',
  from: 'From',
  to: 'To',
  itemError: 'This item needs attention:',
  noItems: 'No items recorded for this run.',
  mirrorPending: 'Backup rename pending',

  scan: 'Scan & propose',
  scanHelp:
    'Scan torrent/New, match torrents and propose a category and show folder. Nothing moves until you apply. A new scan replaces the review plan.',
  auto: 'Apply auto',
  autoHelp:
    'Move only open rows marked Auto eligible, using the saved proposals. Normally these are confident matches to existing show folders, or Movies. Save or discard your review edits first.',
  selected: 'Apply selected',
  selectedHelp:
    'Save your edits and move every selected, unprocessed row. TV and Documentaries need a valid show folder even when unchecked, because all review edits are saved. Unchecked rows stay in New.',
  links: 'Rebuild links',
  linksHelp:
    'Rebuild the cleaned symlink library used by Movies & TV and the am command. This does not move the original media.',
  reviewHelp:
    'Choose a category and show folder, then select the rows to move with Apply selected. Confidence describes the classifier, not whether a move will succeed.',
  autoEligible: 'Auto eligible',
  autoEligibleHelp:
    'This saved proposal meets your automatic-move policy. Apply auto uses the saved proposal, not unsaved review edits.',
  editedHelp:
    'You changed this row. Apply selected saves and uses these edits; Apply auto is unavailable until edits are saved or discarded.',
  moved: 'Moved',
  unconfirmed: 'Unconfirmed',
  movedHelp:
    'This row was confirmed moved. It is locked to prevent a second move. Use run history to undo eligible items.',
  unconfirmedHelp:
    'The move has not been confirmed. Check qBittorrent and run history before scanning again; the item may still be in New. It is locked to prevent a second move.',

  confidenceHelp:
    'Classifier confidence in the proposed category and show. Automatic eligibility also depends on your settings and whether the show folder exists.',
  destinationHelp:
    'Choose TV, Documentaries or Movies. TV and Documentaries use a show folder; select an existing name or type a new one.',
  selectHelp:
    'Checked rows are moved by Apply selected. Unchecked rows remain in New. Already processed rows cannot be selected again.',
  undoHelp:
    'Move this run’s items back to New, only if each is still where the run left it. Backup copies are restored where possible; changed files are refused.',
  undoConfirm:
    'Undo this run? Items still in their recorded destinations will return to New. Changed items cannot be undone.',
  settingsHelp:
    'Switches and menus save immediately. Text and numbers save on blur or Enter. Settings affect the next scan or run.',
  mirrorHelp:
    'Matching backup copies wait while bigboi is unmounted. They are renamed before the next backup sync, avoiding a full recopy.',
  noPlan: 'No review plan yet. Scan & propose to inspect New; scanning moves nothing.',
  stalePlan: 'This plan is no longer editable. Scan & propose for a fresh review.',
  dirty: 'Review edits are not saved yet. Apply selected saves them before moving files.',
  refresh: 'Refresh status',
  loadError:
    'Could not refresh the organizer. Actions are paused; refresh status before continuing.',
};
export const stageLabels = {
  [Stage.Scan]: 'Scanning',
  [Stage.Propose]: 'Proposing',
  [Stage.Apply]: 'Applying',
  [Stage.Undo]: 'Undoing',
  [Stage.Alias]: 'Rebuilding links',
  [Stage.Mirror]: 'Renaming backup copies',
};
export const skipLabels = {
  [SkipReason.Incomplete]: 'Download not finished — wait for it to complete, then scan again.',
  [SkipReason.Moving]: 'Torrent is moving — wait for qBittorrent, then scan again.',
  [SkipReason.Unreadable]: 'File could not be read — check its availability and permissions.',
  [SkipReason.NotMedia]: 'No supported media found — left in New.',
  [SkipReason.Hidden]:
    'Hidden file (Finder ._ metadata or qBittorrent partial data) — never moved.',
  [SkipReason.Nested]:
    'qBittorrent saves this torrent inside this folder, or several torrents share it — fix its location in qBittorrent, then scan again.',
};
export const aliasLabels = {
  [AliasState.Scheduled]: 'Rebuild queued',
  [AliasState.Skipped]: 'Rebuild not requested',
  [AliasState.Failed]: 'Rebuild failed',
};
export const classifierLabels = {
  [Classifier.Codex]: 'Codex on Pi',
  [Classifier.Offline]: 'Filename rules only',
};
export const settingFields = {
  auto_apply: {
    label: 'Auto apply after downloads',
    help: 'After each download finishes, scan New, classify it and move eligible rows without asking. Other rows wait for review.',
  },
  auto_apply_new_shows: {
    label: 'Auto apply new shows',
    help: 'Allow automatic TV and Documentary moves to create new show folders. When off, new shows wait for your review.',
  },
  mirror_rename: {
    label: 'Rename backup copies',
    help: 'Rename matching copies on bigboi so the next backup sync does not recopy them. If the drive is unmounted, renames wait until the next sync.',
  },
  run_alias_after_apply: {
    label: 'Rebuild links after apply',
    help: 'Refresh the cleaned symlink library after moving files so Movies & TV and the am command can find them.',
  },
  auto_apply_min_confidence: {
    label: 'Minimum auto confidence',
    help: 'A score from 0 to 1 required for automatic moves: 0.85 means 85%. Lower-confidence rows wait for review. A high score is not a guarantee; show-folder rules also apply.',
  },
  classifier: {
    label: 'Classifier',
    help: 'Codex asks Codex on the Pi using your ChatGPT login, once per scan. Filename rules work offline and identify SxxEyy names as TV.',
  },
  codex_model: {
    label: 'Codex model',
    help: 'Optional model override for the Pi’s Codex call. Leave blank to use its default. Ignored when using filename rules.',
  },
};
export const tileCopy = {
  title: 'Movies, TV, Audiobooks',
  library: 'Open the library',
  organizer: 'Media organizer',
  watch: 'Watchlist',
  books: 'Audiobooks',
  organizerHelp: 'Review and file completed downloads from New into TV, Documentaries or Movies.',
  watchHelp:
    'Find newer 1080p x264/H.264 releases for wanted shows. A release may be one episode or a whole season.',
  newHelp:
    'Entries currently in torrent/New, including unfinished downloads and non-media files. Review plans are snapshots and may have different counts.',
};
export function operationError(label: string, reason: unknown) {
  const detail = reason instanceof Error ? reason.message : String(reason);
  return `${label} could not be confirmed. ${detail} Refresh status and check the result before retrying.`;
}

export const itemLabels = {
  [ItemState.Moved]: 'Moved',
  [ItemState.Failed]: 'Needs attention',
  [ItemState.Reverted]: 'Returned to New',
};
