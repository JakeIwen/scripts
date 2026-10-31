import { useEffect, useState } from 'react';
import * as api from './api';
import type { Plan, Decisions, OrganizerStatus } from './schema';
import { operationError } from './content';

export function usePlanReview(open: boolean, status: OrganizerStatus | null) {
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
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
        if (!controller.signal.aborted) setError(operationError('Loading the review plan', reason));
      });
    return () => controller.abort();
  }, [open, planId, completed, planState, revision]);
  return { error, plan, setPlan, draft, setDraft, reload: () => setRevision((value) => value + 1) };
}
