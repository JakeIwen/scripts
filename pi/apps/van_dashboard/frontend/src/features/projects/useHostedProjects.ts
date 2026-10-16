import { useEffect, useRef, useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import { fetchHostedProjects, type ProjectPatch, updateHostedProject } from './api';
import type { HostedProject } from './schema';

export type UpdateProject = (
  project: HostedProject,
  patch: ProjectPatch,
  successMessage: string,
) => Promise<boolean>;

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export function useHostedProjects(open: boolean) {
  const [projects, setProjects] = useState<HostedProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [error, setError] = useState('');
  const updateInFlight = useRef(false);
  const { showToast } = useToast();

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void fetchHostedProjects(controller.signal)
      .then((nextProjects) => {
        if (!controller.signal.aborted) setProjects(nextProjects);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason, 'Could not load projects'));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open]);

  const updateProject: UpdateProject = async (project, patch, successMessage) => {
    if (updateInFlight.current) return false;
    updateInFlight.current = true;
    setUpdating(true);
    setError('');
    try {
      setProjects(await updateHostedProject(project.id, patch));
      showToast(successMessage);
      return true;
    } catch (reason) {
      const message = errorMessage(reason, 'Could not update project');
      setError(message);
      showToast(message, 'error');
      return false;
    } finally {
      updateInFlight.current = false;
      setUpdating(false);
    }
  };

  const replaceProjects = (nextProjects: HostedProject[]) => {
    setProjects(nextProjects);
    setError('');
  };

  return { projects, loading, updating, error, updateProject, replaceProjects };
}
