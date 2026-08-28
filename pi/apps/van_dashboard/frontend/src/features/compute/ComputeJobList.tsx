import { formatBytes, formatRelativeTime } from '../../utils/format';
import { COMPUTE_JOB_ID_PATTERN } from './detailDecoders';
import { computeJobStateLabel, formatComputeDuration } from './presentation';
import type { ComputeExplorer } from './useComputeExplorer';

function timestamp(job: ComputeExplorer['jobs'][number]): number | null {
  return job.finishedAt ?? job.startedAt ?? job.submittedAt;
}

function JobDetails({ jobId, explorer }: { jobId: string; explorer: ComputeExplorer }) {
  const detail = explorer.detail(jobId);
  const error = explorer.detailError(jobId);
  if (error) return <p className="compute-job-detail-error">{error}</p>;
  if (!detail) return <p className="compute-sheet__empty">Loading job details…</p>;
  const diagnostics = [
    ['Worker error', detail.diagnostics.workerError],
    ['Resource limit', detail.diagnostics.resourceLimit],
    ['Resource monitor', detail.diagnostics.resourceMonitorError],
  ] as const;
  return (
    <div className="compute-job-details">
      <dl>
        <div>
          <dt>Job ID</dt>
          <dd>{detail.job.id}</dd>
        </div>
        <div>
          <dt>Exit code</dt>
          <dd>{detail.exitCode ?? '—'}</dd>
        </div>
        <div>
          <dt>Classification</dt>
          <dd>{detail.diagnostics.failureClassification ?? '—'}</dd>
        </div>
        <div>
          <dt>Queue delay</dt>
          <dd>{formatComputeDuration(detail.job.queueSeconds)}</dd>
        </div>
      </dl>
      {diagnostics.map(
        ([label, diagnostic]) =>
          diagnostic.text && (
            <section key={label}>
              <h4>
                {label}
                {diagnostic.truncated ? ' (truncated)' : ''}
              </h4>
              <pre>{diagnostic.text}</pre>
            </section>
          ),
      )}
      {(
        [
          ['stderr', detail.stderr],
          ['stdout', detail.stdout],
        ] as const
      ).map(([label, output]) =>
        output.available && output.excerpt ? (
          <section key={label}>
            <h4>
              {label}{' '}
              <small>
                {output.truncated
                  ? `tail of ${formatBytes(output.bytes)}`
                  : formatBytes(output.bytes)}
              </small>
            </h4>
            <pre>{output.excerpt}</pre>
          </section>
        ) : null,
      )}
    </div>
  );
}

export function ComputeJobList({ explorer }: { explorer: ComputeExplorer }) {
  if (!explorer.jobs.length) return <p className="compute-sheet__empty">No matching jobs.</p>;
  return (
    <div className="compute-job-list">
      {explorer.jobs.map((job) => {
        const observed = timestamp(job);
        const expanded = explorer.expandedJobIds.has(job.id);
        const detailsAvailable = COMPUTE_JOB_ID_PATTERN.test(job.id);
        return (
          <article className={`compute-job compute-job--${job.state}`} key={job.id}>
            <header>
              <span>
                <strong>{job.task}</strong>
                <small>
                  {observed === null ? 'Unknown time' : formatRelativeTime(observed)} ·{' '}
                  {job.worker ?? job.placement}
                </small>
              </span>
              <b>{computeJobStateLabel(job.state)}</b>
            </header>
            {job.failureSummary && <p className="compute-job__failure">{job.failureSummary}</p>}
            <p>
              {job.telemetryAvailable
                ? `${formatComputeDuration(job.cpuSeconds)} CPU · ${formatComputeDuration(job.activeSeconds)} active · ${formatBytes(job.peakResidentBytes)} peak`
                : `${formatBytes(job.inputBytes)} input · resource telemetry unavailable`}
            </p>
            <button
              type="button"
              className="compute-job__details-button"
              disabled={!detailsAvailable}
              aria-expanded={expanded}
              onClick={() => explorer.toggleDetails(job.id)}
            >
              {expanded ? 'Hide details' : 'Details'}
            </button>
            {expanded && <JobDetails jobId={job.id} explorer={explorer} />}
          </article>
        );
      })}
    </div>
  );
}
