import { Stage, type OrganizerStatus } from './schema';

import { stageLabels } from './content';

function elapsedLabel(startedAt: string, now: number): string | null {
  const seconds = Math.round((now - Date.parse(startedAt)) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** Live progress of the organizer job the CLI is running, then its outcome. */
export function JobProgress({
  status,
  now = Date.now(),
  pendingStage,
  showOutcome = true,
}: {
  status: OrganizerStatus;
  now?: number;
  pendingStage?: (typeof Stage)[keyof typeof Stage] | null;
  showOutcome?: boolean;
}) {
  const { busy, job } = status;
  const stage = busy?.stage ?? (job && !job.finished_at ? job.stage : pendingStage);
  if (!stage) return showOutcome ? <FinishedJob status={status} /> : null;
  const label = stageLabels[stage];
  const startedAt = busy?.started_at ?? job?.started_at;
  const elapsed = startedAt ? elapsedLabel(startedAt, now) : null;
  const total = busy?.total;
  const done = busy?.done ?? 0;
  const live = stage === Stage.Apply ? status.recent_runs.find((run) => !run.finished_at) : null;
  return (
    <div className="organizer-progress" role="status" aria-live="polite">
      <p>
        <strong>{label}…</strong>
        {busy?.detail && <span> {busy.detail}</span>}
        {elapsed && <small> · {elapsed}</small>}
      </p>
      {total ? (
        <progress value={done} max={total} aria-label={`${label} progress`} />
      ) : (
        <progress aria-label={`${label} progress`} />
      )}
      {!!total && (
        <small>
          {done} of {total}
          {live && ` · ${live.moved_count} moved · ${live.failed_count} failed`}
        </small>
      )}
    </div>
  );
}

function FinishedJob({ status }: { status: OrganizerStatus }) {
  const job = status.job;
  if (!job?.finished_at || !job.ok || !job.result_id) return null;
  const run = status.recent_runs.find((candidate) => candidate.id === job.result_id);
  let outcome: string | null = null;
  if (job.stage === Stage.Apply && run) {
    outcome = `Apply finished: ${run.moved_count} moved · ${run.failed_count} failed`;
  } else if (job.stage === Stage.Undo && run) {
    outcome = `Undo finished: ${run.reverted_count} reverted · ${run.failed_count} failed`;
  } else if (job.stage === Stage.Propose && status.latest_plan?.id === job.result_id) {
    const plan = status.latest_plan;
    outcome = `Plan ready: ${plan.row_count} rows · ${plan.accepted_count} proposed`;
  }
  return outcome ? <p role="status">{outcome}</p> : null;
}
