import { useEffect, useState } from 'react';
import { usePollingResource } from '../../hooks/usePollingResource';
import { useSingleFlightAction } from '../../hooks/useSingleFlightAction';
import { useToast } from '../../components/ToastProvider';
import * as api from './api';
import {
  Category,
  Stage,
  type Decisions,
  type Plan,
  type Settings,
  type OrganizerStatus,
} from './schema';
import { rowDecision } from './ReviewTable';

export function useOrganizer(open: boolean) {
  const resource = usePollingResource({ load: api.fetchStatus, intervalMs: open ? 2000 : 15000 });
  const [error, setError] = useState<string | null>(null);
  const { running, run } = useSingleFlightAction();
  const { showToast } = useToast();
  const status = resource.data;
  const review = usePlanReview(open, status, setError);
  const { plan, setPlan, draft, setDraft } = review;
  const busyStage =
    status?.busy?.stage ?? (status?.job && !status.job.finished_at ? status.job.stage : null);
  const blocked = running || !!busyStage || !status;

  async function perform(action: () => Promise<unknown>, message?: string): Promise<boolean> {
    if (blocked) return false;
    const result = await run(async () => {
      setError(null);
      try {
        await action();
        if (message) showToast(message);
        await resource.refresh();
        return true;
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : String(reason);
        setError(message);
        showToast(message, 'error');
        return false;
      }
    });
    return result ?? false;
  }
  const openRows = plan ? reviewableRows(plan) : [];
  async function applySelected() {
    if (!plan) return;
    const decisions: Decisions = Object.fromEntries(
      openRows.map((row) => [row.entry.name, draft[row.entry.name] ?? rowDecision(row)]),
    );
    await perform(async () => {
      const saved = await api.decide(plan.id, decisions);
      setPlan(saved);
      setDraft({});
      await api.startJob(Stage.Apply, { plan_id: saved.id, auto: false });
    });
  }
  return {
    resource,
    plan,
    draft,
    error,
    blocked,
    busyStage,
    clearDraft: () => setDraft({}),
    change: (name: string, decision: Decisions[string]) =>
      setDraft((current) => ({ ...current, [name]: decision })),
    scan: () => perform(() => api.startJob(Stage.Propose, {})),
    applySelected,
    applyAuto: () =>
      plan && perform(() => api.startJob(Stage.Apply, { plan_id: plan.id, auto: true })),
    undo: (run_id: string) => perform(() => api.startJob(Stage.Undo, { run_id })),
    alias: () => perform(api.rebuildLinks, 'Link rebuild scheduled'),
    save: (changes: Partial<Settings>) =>
      perform(() => api.saveSetting(changes), 'Organizer setting saved'),
    canApply:
      openRows.some((row) => (draft[row.entry.name] ?? rowDecision(row)).accepted) &&
      openRows.every((row) => {
        const d = draft[row.entry.name] ?? rowDecision(row);
        return !d.accepted || d.category === Category.Movies || !!d.show?.trim();
      }),
  };
}

/** Rows still open for review: not skipped and not already moved by a run. */
export function reviewableRows(plan: Plan) {
  const settled = new Set(plan.settled_rows);
  return plan.rows.filter((row) => !row.entry.skip_reason && !settled.has(row.entry.name));
}
export type Organizer = ReturnType<typeof useOrganizer>;

function usePlanReview(
  open: boolean,
  status: OrganizerStatus | null,
  setError: (error: string | null) => void,
) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [draft, setDraft] = useState<Decisions>({});
  const planId = status?.latest_plan?.id;
  const completed = status?.job?.finished_at;
  const planState = status?.latest_plan?.status;
  useEffect(() => setDraft({}), [planId]);
  useEffect(() => {
    if (!open || !planId) return;
    const controller = new AbortController();
    void api
      .fetchPlan(planId, controller.signal)
      .then((next) => {
        if (controller.signal.aborted) return;
        setPlan(next);
        setError(null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(String(reason));
      });
    return () => controller.abort();
  }, [open, planId, completed, planState, setError]);
  return { plan, setPlan, draft, setDraft };
}
