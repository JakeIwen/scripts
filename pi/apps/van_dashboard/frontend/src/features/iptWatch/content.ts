import { CandidateAction, CandidateState, Login, Source, WantAction, JobStage } from './schema';
export const watchCopy = {
  unknownOutcome:
    'Check outcome unknown: the dashboard no longer reports the accepted check. Check the last-check time and release history before checking again. Nothing was replayed.',
  check: 'Check now',
  checkHelp:
    'Search IPTorrents once per active wanted show. Requests are spaced 2.5 seconds apart and cached for 6 hours, so a repeat check may reuse results. Scheduled checks also run every 6 hours.',
  checking:
    'Checking wanted shows… Cached searches may finish immediately; uncached shows are searched one at a time.',
  releasesHelp:
    'Only 1080p x264/H.264 TV releases newer than what is on disk are listed. Multiple releases can cover the same episode; season packs may overlap episodes you already have.',
  downloadHelp:
    'Save this release’s .torrent to the Pi’s watched tfiles folder. qBittorrent starts the download; the organizer can file it after completion. This does not mean the video has finished downloading.',
  dismissHelp: 'Hide this release from the new list. It stays visible under Show handled.',
  wantedHelp:
    'Library shows watched within 90 days or up next are included automatically. Add other shows manually. Pause excludes a show from future checks; its new releases disappear on the next check, but existing downloads are untouched.',
  manualHelp:
    'A show you added here. Remove hides it from the watchlist, including its library entry if present. Add it again to restore it.',
  libraryHelp:
    'Included from the video library because it was watched within 90 days or is up next.',
  positionHelp:
    'The newest season and episode you already have, not what you want to download. Leave both blank if unknown. An episode needs a season; with a season alone, the last episode is unknown, so all episodes in that season can still appear.',
  pauseHelp:
    'Stop searching for this show. Its new releases disappear on the next check; downloads already started are left alone.',
  resumeHelp: 'Include this show in the next scheduled or manual check.',
  removeHelp:
    'Remove this show from the watchlist, including any library-derived entry. Add it again to restore it. No media is removed and downloads are not cancelled.',
  freeleechHelp:
    'The site marks this release as free-leech. Check the tracker’s current rules before relying on this flag.',
  packHelp:
    'A full season, which can include episodes already on disk. Download asks for confirmation.',
  loginNeeded:
    'IPTorrents login has expired or is missing. Refresh the Pi’s saved login cookies before checking or downloading.',
  loginUnknown: 'Login has not been verified yet. Check now to test the saved login.',
  loadError:
    'Could not refresh the watchlist. Actions are paused; refresh status before continuing.',
  seasonError: 'Enter a season when specifying an episode.',
  numberError: 'Season and episode must be whole numbers, zero or greater.',
};
export const loginLabels = {
  [Login.Ok]: 'Login ok',
  [Login.Needed]: 'Login needed',
  [Login.Unknown]: 'Login unknown',
};
export const sourceLabels = { [Source.Library]: 'Library', [Source.Manual]: 'Manual' };
export const candidateLabels = {
  [CandidateState.New]: 'New',
  [CandidateState.Downloaded]: 'Torrent sent',
  [CandidateState.Dismissed]: 'Dismissed',
};
export const pendingLabels = {
  [CandidateAction.Download]: 'Sending torrent…',
  [CandidateAction.Dismiss]: 'Dismissing…',
  [WantAction.Add]: 'Adding…',
  [WantAction.Remove]: 'Removing…',
  [WantAction.Pause]: 'Pausing…',
  [WantAction.Resume]: 'Resuming…',
  [JobStage.Check]: 'Checking…',
};
export const successLabels = {
  [CandidateAction.Download]:
    'Torrent sent to qBittorrent’s watch folder. The video download is not finished yet.',
  [CandidateAction.Dismiss]: 'Release dismissed.',
  [WantAction.Add]: 'Show added.',
  [WantAction.Remove]: 'Manual show removed.',
  [WantAction.Pause]: 'Show paused for future checks.',
  [WantAction.Resume]: 'Show resumed for future checks.',
};

export function releaseCount(count: number) {
  return `${count} new ${count === 1 ? 'release' : 'releases'}`;
}
