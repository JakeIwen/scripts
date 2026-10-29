import { BottomSheet } from '../../components/BottomSheet';
import { formatRelativeTime } from '../../utils/format';
import { Candidates } from './Candidates';
import { WantedShows } from './WantedShows';
import { Login } from './schema';
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
  const status = w.resource.data;
  const loginText = {
    [Login.Ok]: 'Login ok',
    [Login.Needed]: 'Login needed — log in to iptorrents.com in Firefox and run the cookie script',
    [Login.Unknown]: `Login unknown${status?.last_error ? ` — ${status.last_error}` : ' — not checked yet'}`,
  };
  const error = w.error ?? w.resource.error?.message ?? status?.job?.error ?? status?.last_error;
  return (
    <BottomSheet open={open} onClose={onClose} title="Watchlist" className="ipt-watch-sheet">
      <div className="ipt-watch-toolbar">
        <span>{status ? loginText[status.login] : 'Loading watchlist…'}</span>
        <span>
          Last check{' '}
          {status?.last_check ? formatRelativeTime(Date.parse(status.last_check) / 1000) : 'never'}
        </span>
        <button type="button" disabled={w.blocked} onClick={() => void w.check()}>
          {w.checking ? 'Checking…' : 'Check now'}
        </button>
        <span>{status?.new_candidate_count ?? 0} new candidates</span>
      </div>
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      {status && (
        <>
          {!status.tfiles_writable && (
            <p className="error-message">
              Torrent watch folder is not writable. Downloads are disabled.
            </p>
          )}
          {!status.library_available && (
            <p>Library unavailable; library-derived shows may be out of date.</p>
          )}
          <WantedShows shows={status.wanted} disabled={w.blocked} change={w.wantlist} />
          <Candidates
            candidates={status.candidates}
            disabled={w.blocked}
            writable={status.tfiles_writable}
            change={w.candidate}
          />
        </>
      )}
    </BottomSheet>
  );
}
