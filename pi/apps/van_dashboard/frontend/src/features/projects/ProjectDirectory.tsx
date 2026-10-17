import { BottomSheet } from '../../components/BottomSheet';
import { NewProjectForm } from './NewProjectForm';
import { ProjectRow } from './ProjectRow';
import { PROJECT_STATUS, type HostedProject } from './schema';
import { type UpdateProject, useHostedProjects } from './useHostedProjects';
import './projects.css';

export function ProjectDirectory({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { projects, loading, updating, error, updateProject, replaceProjects } =
    useHostedProjects(open);
  // Only projects whose last scan got a web page (HTTP 200) are shown; the
  // store keeps the rest so they return when they serve again.
  const shown = projects.filter((project) => project.live);
  const newProjects = shown.filter((project) => project.status === PROJECT_STATUS.NEW);
  const listedProjects = shown.filter((project) => project.status === PROJECT_STATUS.LISTED);
  const hiddenProjects = shown.filter((project) => project.status === PROJECT_STATUS.HIDDEN);
  const rowProps = { disabled: loading || updating, onUpdate: updateProject };

  return (
    <BottomSheet
      open={open}
      title="Hosted projects"
      className="project-directory-sheet"
      onClose={onClose}
    >
      {newProjects.length > 0 && (
        <ProjectGroup title="New" projects={newProjects} rowProps={rowProps} />
      )}
      <ProjectGroup title="Listed" projects={listedProjects} rowProps={rowProps} />
      {hiddenProjects.length > 0 && (
        <details className="project-directory__hidden">
          <summary>Hidden ({hiddenProjects.length})</summary>
          <ul className="project-directory__list">
            {hiddenProjects.map((project) => (
              <ProjectRow key={project.id} project={project} {...rowProps} />
            ))}
          </ul>
        </details>
      )}
      <p className="project-directory__legend">
        LAN: van network · TS: Tailscale · This Mac: local apps
      </p>
      {error && (
        <p className="error-message" role="alert">
          {error}
        </p>
      )}
      <NewProjectForm
        projects={projects}
        disabled={loading || updating || Boolean(error)}
        onSaved={replaceProjects}
      />
    </BottomSheet>
  );
}

function ProjectGroup({
  title,
  projects,
  rowProps,
}: {
  title: string;
  projects: HostedProject[];
  rowProps: { disabled: boolean; onUpdate: UpdateProject };
}) {
  return (
    <section className="project-directory__group">
      <h3>{title}</h3>
      <ul className="project-directory__list">
        {projects.map((project) => (
          <ProjectRow key={project.id} project={project} {...rowProps} />
        ))}
      </ul>
    </section>
  );
}
