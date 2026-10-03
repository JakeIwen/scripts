import { useEffect, useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import { HOSTED_PROJECTS, preferredProjectLink, type HostedProject } from './catalog';
import { fetchHostedProjects } from './api';
import { NewProjectForm } from './NewProjectForm';
import './projects.css';

export function ProjectDirectory({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [saved, setSaved] = useState<HostedProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError('');
    void fetchHostedProjects(controller.signal)
      .then((projects) => {
        if (!controller.signal.aborted) setSaved(projects);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : 'Could not load saved projects');
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open]);
  const projects = [...HOSTED_PROJECTS, ...saved].sort((a, b) => a.name.localeCompare(b.name));
  return (
    <BottomSheet
      open={open}
      title="Hosted projects"
      className="project-directory-sheet"
      onClose={onClose}
    >
      <ul className="project-directory__list">
        {projects.map((project) => {
          const preferred = preferredProjectLink(project.links);
          return (
            <li className="project-directory__row" key={project.id}>
              <a
                className="project-directory__primary"
                href={preferred?.url}
                target="_blank"
                rel="noopener noreferrer"
                title={[project.name, project.description, project.note, preferred?.url]
                  .filter(Boolean)
                  .join(' ')}
              >
                {project.name}
              </a>
              <div className="project-directory__links">
                {project.links.map((link) => (
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    key={link.label}
                    title={[link.url, link.note].filter(Boolean).join(' · ')}
                    aria-label={[project.name, link.label, link.url, link.note]
                      .filter(Boolean)
                      .join(' · ')}
                  >
                    {link.label === 'Tailscale' ? 'TS' : link.label}
                  </a>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
      <p className="project-directory__legend">
        LAN: van network · TS: Tailscale · This Mac: local apps · * Not live yet
      </p>
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      <NewProjectForm projects={projects} disabled={loading || Boolean(error)} onSaved={setSaved} />
    </BottomSheet>
  );
}
