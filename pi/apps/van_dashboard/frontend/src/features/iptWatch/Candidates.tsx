import { useState } from 'react';
import { formatBytes } from '../../utils/format';
import { CandidateAction, CandidateState, type Candidate, type CandidateOperation } from './schema';

export function Candidates({
  candidates,
  disabled,
  writable,
  change,
}: {
  candidates: Candidate[];
  disabled: boolean;
  writable: boolean;
  change: (action: CandidateOperation, candidate: Candidate) => Promise<boolean>;
}) {
  const [handled, setHandled] = useState(false);
  const fresh = candidates.filter((candidate) => candidate.status === CandidateState.New);
  const old = candidates.filter((candidate) => candidate.status !== CandidateState.New);
  return (
    <section className="ipt-watch-section">
      <h3>Candidates</h3>
      {fresh.length === 0 && <p>No new candidates.</p>}
      <CandidateGroups candidates={fresh} disabled={disabled} writable={writable} change={change} />
      {old.length > 0 && (
        <>
          <button type="button" aria-expanded={handled} onClick={() => setHandled(!handled)}>
            {handled ? 'Hide handled' : 'Show handled'} ({old.length})
          </button>
          {handled && (
            <CandidateGroups
              candidates={old}
              disabled={disabled}
              writable={writable}
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
  change,
}: {
  candidates: Candidate[];
  disabled: boolean;
  writable: boolean;
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
            <article className="ipt-watch-row" key={candidate.torrent_id}>
              <div className="ipt-watch-details">
                <strong>{candidate.name}</strong>
                <div className="ipt-watch-badges">
                  {candidate.episode === null && (
                    <span className="ipt-watch-badge">Season pack</span>
                  )}
                  {candidate.freeleech && <span className="ipt-watch-badge">Free-leech</span>}
                  <span className="ipt-watch-badge">{candidate.category}</span>
                  {candidate.status !== CandidateState.New && (
                    <span className="ipt-watch-badge">{candidate.status}</span>
                  )}
                </div>
                <p>
                  {formatBytes(candidate.size_bytes)} · {candidate.seeders} seeders /{' '}
                  {candidate.leechers} leechers · {candidate.age_text}
                </p>
                {candidate.error && <p className="error-message">{candidate.error}</p>}
              </div>
              {candidate.status === CandidateState.New && (
                <div className="ipt-watch-actions">
                  <button
                    type="button"
                    disabled={disabled || !writable}
                    aria-label={`Download ${candidate.name}`}
                    onClick={() => void change(CandidateAction.Download, candidate)}
                  >
                    Download
                  </button>
                  <button
                    type="button"
                    disabled={disabled}
                    aria-label={`Dismiss ${candidate.name}`}
                    onClick={() => void change(CandidateAction.Dismiss, candidate)}
                  >
                    Dismiss
                  </button>
                </div>
              )}
            </article>
          ))}
        </section>
      ))}
    </>
  );
}
