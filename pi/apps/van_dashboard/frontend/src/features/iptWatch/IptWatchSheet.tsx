import { useState } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { Candidates } from './Candidates';
import { WantedShows } from './WantedShows';
import { WatchStatus } from './WatchStatus';
import type { IptWatch } from './useIptWatch';
import './iptWatch.css';

export function IptWatchSheet({
  open,
  onClose,
  watch: w,
}: {
  open: boolean;
  onClose: () => void;
  watch: IptWatch;
}) {
  const [wanted, setWanted] = useState(false);
  const status = w.resource.data;
  return (
    <BottomSheet open={open} onClose={onClose} title="Watchlist" className="ipt-watch-sheet">
      <WatchStatus watch={w} />
      <nav className="media-navigation" aria-label="Watchlist views">
        <button
          type="button"
          className="secondary-button"
          aria-pressed={!wanted}
          onClick={() => setWanted(false)}
        >
          New releases ({status?.new_candidate_count ?? 0})
        </button>
        <button
          type="button"
          className="secondary-button"
          aria-pressed={wanted}
          onClick={() => setWanted(true)}
        >
          Wanted shows ({status?.wanted.length ?? 0})
        </button>
      </nav>
      {status && (
        <>
          {!status.tfiles_writable && (
            <p className="error-message">
              Torrent watch folder is not writable. Restore access to the Pi’s tfiles folder before
              downloading.
            </p>
          )}
          {!status.library_available && (
            <p className="media-notice">
              Library unavailable; library-derived shows may be out of date.
            </p>
          )}
          <div hidden={!wanted}>
            <WantedShows
              shows={status.wanted}
              disabled={w.blocked}
              pending={w.pending}
              change={w.wantlist}
            />
          </div>
          <div hidden={wanted}>
            <Candidates
              candidates={status.candidates}
              disabled={w.blocked}
              pending={w.pending}
              writable={status.tfiles_writable}
              change={w.candidate}
            />
          </div>
        </>
      )}
    </BottomSheet>
  );
}
