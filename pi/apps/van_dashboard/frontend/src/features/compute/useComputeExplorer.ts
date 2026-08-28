import { useEffect, useMemo, useRef, useState } from 'react';

import { fetchComputeJobDetails, fetchComputeTaskJobs } from './api';
import type { ComputeJobDetails, ComputeJobSummary, ComputeRange, ComputeTaskJobs } from './types';

export interface ComputeExplorer {
  selectedTask: string | null;
  jobs: ComputeJobSummary[];
  taskLoading: boolean;
  taskError: string | null;
  expandedJobIds: ReadonlySet<string>;
  detail: (jobId: string) => ComputeJobDetails | null;
  detailLoading: (jobId: string) => boolean;
  detailError: (jobId: string) => string | null;
  toggleTask: (task: string) => void;
  toggleDetails: (jobId: string) => void;
}

export function useComputeExplorer(
  rangeHours: ComputeRange,
  recentJobs: readonly ComputeJobSummary[],
): ComputeExplorer {
  const taskCache = useRef(new Map<string, ComputeTaskJobs>());
  const taskRequests = useRef(new Map<string, Promise<ComputeTaskJobs>>());
  const detailCache = useRef(new Map<string, ComputeJobDetails>());
  const detailRequests = useRef(new Map<string, Promise<ComputeJobDetails>>());
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  const [taskLoading, setTaskLoading] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);
  const [expandedJobIds, setExpandedJobIds] = useState<Set<string>>(() => new Set());
  const [detailErrors, setDetailErrors] = useState<Map<string, string>>(() => new Map());
  const [, renderCacheChange] = useState(0);

  useEffect(() => {
    setSelectedTask(null);
    setTaskError(null);
    setExpandedJobIds(new Set());
  }, [rangeHours]);

  const jobs = useMemo(() => {
    if (!selectedTask) return [...recentJobs];
    const cached = taskCache.current.get(`${rangeHours}\0${selectedTask}`)?.jobs ?? [];
    const merged = new Map<string, ComputeJobSummary>();
    cached.forEach((job) => merged.set(job.id, job));
    recentJobs.filter((job) => job.task === selectedTask).forEach((job) => merged.set(job.id, job));
    return [...merged.values()].sort(
      (left, right) =>
        (right.finishedAt ?? right.startedAt ?? right.submittedAt ?? 0) -
        (left.finishedAt ?? left.startedAt ?? left.submittedAt ?? 0),
    );
  }, [rangeHours, recentJobs, selectedTask, taskLoading]);

  const toggleTask = (task: string) => {
    if (selectedTask === task) {
      setSelectedTask(null);
      setTaskError(null);
      return;
    }
    setSelectedTask(task);
    setTaskError(null);
    const key = `${rangeHours}\0${task}`;
    if (taskCache.current.has(key)) return;
    let request = taskRequests.current.get(key);
    if (!request) {
      request = fetchComputeTaskJobs(rangeHours, task);
      taskRequests.current.set(key, request);
    }
    setTaskLoading(true);
    void request
      .then((result) => {
        taskCache.current.set(key, result);
        renderCacheChange((value) => value + 1);
      })
      .catch((reason: unknown) => {
        setTaskError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        taskRequests.current.delete(key);
        setTaskLoading(false);
      });
  };

  const toggleDetails = (jobId: string) => {
    if (expandedJobIds.has(jobId)) {
      setExpandedJobIds((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
      return;
    }
    setExpandedJobIds((current) => new Set(current).add(jobId));
    if (detailCache.current.has(jobId) || detailRequests.current.has(jobId)) return;
    const request = fetchComputeJobDetails(jobId);
    detailRequests.current.set(jobId, request);
    setDetailErrors((current) => {
      const next = new Map(current);
      next.delete(jobId);
      return next;
    });
    void request
      .then((result) => {
        detailCache.current.set(jobId, result);
        renderCacheChange((value) => value + 1);
      })
      .catch((reason: unknown) => {
        setDetailErrors((current) =>
          new Map(current).set(jobId, reason instanceof Error ? reason.message : String(reason)),
        );
      })
      .finally(() => {
        detailRequests.current.delete(jobId);
        renderCacheChange((value) => value + 1);
      });
  };

  return {
    selectedTask,
    jobs,
    taskLoading,
    taskError,
    expandedJobIds,
    detail: (jobId) => detailCache.current.get(jobId) ?? null,
    detailLoading: (jobId) => detailRequests.current.has(jobId),
    detailError: (jobId) => detailErrors.get(jobId) ?? null,
    toggleTask,
    toggleDetails,
  };
}
