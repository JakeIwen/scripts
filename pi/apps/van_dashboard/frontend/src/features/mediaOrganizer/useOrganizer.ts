import { usePollingResource } from '../../hooks/usePollingResource';
import * as api from './api';
import { Category, Stage, type Decisions, type Plan, type Settings } from './schema';
import { rowDecision } from './ReviewTable';
import { organizerCopy as copy } from './content';
import { usePlanReview } from './usePlanReview';
import { useOrganizerActions } from './useOrganizerActions';

export function useOrganizer(open: boolean) {
  const resource = usePollingResource({ load: api.fetchStatus, intervalMs: open ? 2000 : 15000 });
  const actions = useOrganizerActions(resource);
  const { error, setError, blocked, perform, start } = actions;
  const review = usePlanReview(open, resource.data);
  const { plan, setPlan, draft, setDraft } = review;
  const openRows = plan ? reviewableRows(plan) : [];
  async function applySelected() {
    if (!plan) return;
    await perform(async () => {
      // External CLI/completion-hook runs can settle rows without changing plan status.
      const fresh = await api.fetchPlan(plan.id);
      setPlan(fresh);
      const rows = reviewableRows(fresh);
      const names = new Set(rows.map((row) => row.entry.name));
      setDraft((current) =>
        Object.fromEntries(Object.entries(current).filter(([name]) => names.has(name))),
      );
      const decisions: Decisions = Object.fromEntries(
        rows.map((row) => [row.entry.name, draft[row.entry.name] ?? rowDecision(row)]),
      );
      if (!Object.values(decisions).some((decision) => decision.accepted)) {
        actions.announce(
          'Nothing left to apply. The selected rows were already processed or are no longer selected.',
        );
        return;
      }
      const saved = await api.decide(fresh.id, decisions);
      setPlan(saved);
      setDraft({});
      await start(Stage.Apply, { plan_id: saved.id, auto: false });
    }, copy.selected);
  }
  return {
    resource,
    refresh: () => {
      review.reload();
      return resource.refresh();
    },
    plan,
    draft,
    error: review.error ?? error,
    blocked,
    busyStage: actions.busyStage,
    actionLabel: actions.actionLabel,
    undoRunId: actions.undoRunId,
    clearError: () => setError(null),
    selectedCount: openRows.filter((row) => (draft[row.entry.name] ?? rowDecision(row)).accepted)
      .length,
    clearDraft: () => setDraft({}),
    change: (name: string, decision: Decisions[string]) =>
      setDraft((current) => ({ ...current, [name]: decision })),
    scan: () => perform(() => start(Stage.Propose, {}), copy.scan),
    applySelected,
    applyAuto: () =>
      plan && perform(() => start(Stage.Apply, { plan_id: plan.id, auto: true }), copy.auto),
    undo: (run_id: string) => perform(() => start(Stage.Undo, { run_id }), 'Undo'),
    alias: () => perform(api.rebuildLinks, copy.links, 'Link rebuild scheduled'),
    save: (changes: Partial<Settings>) =>
      perform(() => api.saveSetting(changes), 'Saving setting', 'Organizer setting saved'),
    canApply:
      openRows.some((row) => (draft[row.entry.name] ?? rowDecision(row)).accepted) &&
      openRows.every((row) => {
        const d = draft[row.entry.name] ?? rowDecision(row);
        return d.category === Category.Movies || !!d.show?.trim();
      }),
  };
}

/** Rows still open for review: not skipped and not already moved by a run. */
export function reviewableRows(plan: Plan) {
  const settled = new Set(plan.settled_rows);
  return plan.rows.filter((row) => !row.entry.skip_reason && !settled.has(row.entry.name));
}
export type Organizer = ReturnType<typeof useOrganizer>;
