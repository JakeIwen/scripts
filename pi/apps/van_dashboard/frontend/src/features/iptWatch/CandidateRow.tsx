import { MediaAction } from '../../components/media/MediaAction';
import { StatusPill } from '../../components/StatusPill';
import { formatBytes } from '../../utils/format';
import { watchCopy as copy, candidateLabels, pendingLabels } from './content';
import {
  CandidateAction,
  CandidateState,
  type Candidate,
  type CandidateOperation,
  type WatchPending,
} from './schema';

export function CandidateRow({
  candidate,
  disabled,
  writable,
  pending,
  change,
}: {
  candidate: Candidate;
  disabled: boolean;
  writable: boolean;
  pending?: WatchPending | null;
  change: (action: CandidateOperation, candidate: Candidate) => Promise<boolean>;
}) {
  return (
    <article className="ipt-watch-row" key={candidate.torrent_id}>
      <div className="ipt-watch-details">
        <strong>{candidate.name}</strong>
        <div className="ipt-watch-badges">
          {candidate.episode === null && (
            <StatusPill detail={copy.packHelp}>Season pack</StatusPill>
          )}
          {candidate.freeleech && <StatusPill detail={copy.freeleechHelp}>Free-leech</StatusPill>}
          <span className="ipt-watch-badge">{candidate.category}</span>
          {candidate.status !== CandidateState.New && (
            <StatusPill>{candidateLabels[candidate.status]}</StatusPill>
          )}
        </div>
        <p>
          {formatBytes(candidate.size_bytes)} · {candidate.seeders} seeders / {candidate.leechers}{' '}
          leechers · {candidate.age_text}
        </p>
        {candidate.error && <p className="error-message">{candidate.error}</p>}
      </div>
      {candidate.status === CandidateState.New && (
        <div className="ipt-watch-actions" aria-busy={pending?.id === candidate.torrent_id}>
          <MediaAction
            label={`Download ${candidate.name}`}
            help={copy.downloadHelp}
            disabled={disabled || !writable}
            aria-label={`Download ${candidate.name}`}
            onClick={() => void change(CandidateAction.Download, candidate)}
          >
            {pending?.id === candidate.torrent_id && pending.action === CandidateAction.Download
              ? pendingLabels[pending.action]
              : 'Download'}
          </MediaAction>
          <MediaAction
            label={`Dismiss ${candidate.name}`}
            help={copy.dismissHelp}
            disabled={disabled}
            aria-label={`Dismiss ${candidate.name}`}
            onClick={() => void change(CandidateAction.Dismiss, candidate)}
          >
            {pending?.id === candidate.torrent_id && pending.action === CandidateAction.Dismiss
              ? pendingLabels[pending.action]
              : 'Dismiss'}
          </MediaAction>
        </div>
      )}
    </article>
  );
}
