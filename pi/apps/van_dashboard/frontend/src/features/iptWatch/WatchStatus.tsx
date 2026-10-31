import { StatusPill } from '../../components/StatusPill';
import { MediaAction } from '../../components/media/MediaAction';
import { formatRelativeTime } from '../../utils/format';
import { Login } from './schema';
import { watchCopy as copy, loginLabels, releaseCount } from './content';
import type { IptWatch } from './useIptWatch';

export function WatchStatus({ watch: w }: { watch: IptWatch }) {
  const status = w.resource.data;
  const error =
    w.error ??
    (w.resource.error ? copy.loadError : null) ??
    status?.job?.error ??
    status?.last_error;
  return (
    <>
      <div className="ipt-watch-toolbar">
        <MediaAction
          label={copy.check}
          help={copy.checkHelp}
          disabled={w.blocked}
          onClick={() => void w.check()}
        >
          {w.checking ? 'Checking…' : copy.check}
        </MediaAction>
        {status && (
          <StatusPill
            tone={status.login === Login.Ok ? 'good' : 'warning'}
            detail={
              status.login === Login.Ok
                ? 'The most recent site request used a valid login.'
                : status.login === Login.Needed
                  ? copy.loginNeeded
                  : copy.loginUnknown
            }
          >
            {loginLabels[status.login]}
          </StatusPill>
        )}
        <span className="ipt-watch-last-check">
          Last check{' '}
          {status?.last_check ? formatRelativeTime(Date.parse(status.last_check) / 1000) : 'never'}
        </span>
      </div>
      {w.checking && (
        <div className="media-notice" role="status">
          <p>{copy.checking}</p>
          <progress aria-label="Watchlist check progress" />
          {status?.job?.started_at && (
            <small>Started {formatRelativeTime(Date.parse(status.job.started_at) / 1000)}</small>
          )}
        </div>
      )}
      {!w.checking && status?.job?.finished_at && status.job.ok && (
        <p className="media-notice" role="status">
          Check complete · {releaseCount(status.new_candidate_count)} available.
        </p>
      )}
      {error && (
        <div className="error-message" role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="secondary-button"
            disabled={w.resource.refreshing}
            onClick={() => {
              w.clearError();
              void w.resource.refresh();
            }}
          >
            Refresh status
          </button>
        </div>
      )}
      {!status && !error && <p role="status">Loading watchlist…</p>}
      {status?.login === Login.Needed && <p className="media-notice">{copy.loginNeeded}</p>}
    </>
  );
}
