import { MediaHelp } from '../../components/media/MediaHelp';
import { organizerCopy as copy } from './content';
import type { Organizer } from './useOrganizer';

export function OrganizerNotices({ organizer: o }: { organizer: Organizer }) {
  const status = o.resource.data!;
  return (
    <>
      {(status.job?.error || status.last_error) && (
        <p className="error-message" role="alert">
          Last operation reported: {status.job?.error ?? status.last_error}. Check the plan and run
          history before retrying.
        </p>
      )}
      {!status.media_mounted && (
        <p className="error-message">
          Media drive is not mounted. Restore the mount, then refresh status.
        </p>
      )}
      {!status.qbittorrent_available && (
        <p className="media-notice">
          qBittorrent is unavailable. Tracked entries cannot move until it is running again.
        </p>
      )}
      {status.settings.mirror_rename && status.mirror_pending > 0 && (
        <p className="media-notice">
          {status.mirror_pending} backup {status.mirror_pending === 1 ? 'copy' : 'copies'} waiting
          for rename before the next backup sync.{' '}
          <MediaHelp label="Pending backup renames">{copy.mirrorHelp}</MediaHelp>
        </p>
      )}
    </>
  );
}
