import { useState } from 'react';
import { MediaHelp } from '../../components/media/MediaHelp';
import { watchCopy as copy } from './content';
import { CandidateRow } from './CandidateRow';
import {
  CandidateState,
  type Candidate,
  type CandidateOperation,
  type WatchPending,
} from './schema';

export function Candidates({
  candidates,
  disabled,
  writable,
  pending,
  change,
}: {
  candidates: Candidate[];
  disabled: boolean;
  writable: boolean;
  pending?: WatchPending | null;
  change: (action: CandidateOperation, candidate: Candidate) => Promise<boolean>;
}) {
  const [handled, setHandled] = useState(false);
  const fresh = candidates.filter((candidate) => candidate.status === CandidateState.New);
  const old = candidates.filter((candidate) => candidate.status !== CandidateState.New);
  return (
    <section className="ipt-watch-section">
      <div className="media-section-heading">
        <h3>New releases</h3>
        <MediaHelp label="Release filters">{copy.releasesHelp}</MediaHelp>
      </div>
      {fresh.length === 0 && <p>No new releases. Check now, or add a wanted show.</p>}
      <CandidateGroups
        candidates={fresh}
        disabled={disabled}
        writable={writable}
        pending={pending}
        change={change}
      />
      {old.length > 0 && (
        <>
          <button
            type="button"
            className="secondary-button"
            aria-expanded={handled}
            onClick={() => setHandled(!handled)}
          >
            {handled ? 'Hide handled' : 'Show handled'} ({old.length})
          </button>
          {handled && (
            <CandidateGroups
              candidates={old}
              disabled={disabled}
              writable={writable}
              pending={pending}
              change={change}
            />
          )}
        </>
      )}
    </section>
  );
}
function CandidateGroups({
  candidates,
  disabled,
  writable,
  pending,
  change,
}: {
  candidates: Candidate[];
  disabled: boolean;
  writable: boolean;
  pending?: WatchPending | null;
  change: (action: CandidateOperation, candidate: Candidate) => Promise<boolean>;
}) {
  const groups = new Map<string, Candidate[]>();
  for (const candidate of [...candidates].sort((a, b) =>
    b.first_seen.localeCompare(a.first_seen),
  )) {
    const group = groups.get(candidate.show) ?? [];
    group.push(candidate);
    groups.set(candidate.show, group);
  }
  return (
    <>
      {[...groups].map(([show, rows]) => (
        <section key={show} aria-label={`${show} candidates`}>
          <h4>{show}</h4>
          {rows.map((candidate) => (
            <CandidateRow
              key={candidate.torrent_id}
              candidate={candidate}
              disabled={disabled}
              writable={writable}
              pending={pending}
              change={change}
            />
          ))}
        </section>
      ))}
    </>
  );
}
