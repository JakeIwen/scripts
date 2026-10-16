import { useState } from 'react';

import { PROJECT_URL_FIELDS } from './api';
import { ProjectEditForm } from './ProjectEditForm';
import type { UpdateProject } from './useHostedProjects';
import { preferredProjectLink, PROJECT_HOST, PROJECT_STATUS, type HostedProject } from './schema';

const HOST_LABELS: Record<HostedProject['host'], string> = {
  [PROJECT_HOST.MAC]: 'This Mac',
  [PROJECT_HOST.PI]: 'Vanpi',
  [PROJECT_HOST.WEB]: 'lionfeather.dev',
  [PROJECT_HOST.CUSTOM]: 'Custom',
};

export function ProjectRow({
  project,
  disabled,
  onUpdate,
}: {
  project: HostedProject;
  disabled: boolean;
  onUpdate: UpdateProject;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <li className="project-directory__row project-directory__row--editing">
        <ProjectEditForm
          project={project}
          disabled={disabled}
          onCancel={() => setEditing(false)}
          onSave={(patch) => onUpdate(project, patch, `Updated ${project.name}`)}
        />
      </li>
    );
  }

  const preferred = preferredProjectLink(project.links);
  const rowClass = `project-directory__row${project.live ? '' : ' project-directory__row--stale'}`;
  return (
    <li className={rowClass}>
      <div className="project-directory__identity">
        {preferred ? (
          <a
            className="project-directory__primary"
            href={preferred.url}
            target="_blank"
            rel="noopener noreferrer"
            title={project.description || undefined}
          >
            {project.name}
          </a>
        ) : (
          <span className="project-directory__primary">{project.name}</span>
        )}
        <div className="project-directory__metadata">
          <span className="status-pill project-directory__host">{HOST_LABELS[project.host]}</span>
          {!project.live && <span className="project-directory__offline">offline</span>}
        </div>
      </div>
      <ProjectLinks project={project} />
      <ProjectActions
        project={project}
        disabled={disabled}
        onEdit={() => setEditing(true)}
        onUpdate={onUpdate}
      />
    </li>
  );
}

// Labels follow the link kind so scanned and hand-entered links read the same.
function linkLabel(kind: HostedProject['links'][number]['kind']): string {
  return PROJECT_URL_FIELDS.find((field) => field.kind === kind)?.label ?? kind;
}

function ProjectLinks({ project }: { project: HostedProject }) {
  return (
    <div className="project-directory__links">
      {project.links.map((link) => (
        <a
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          key={`${link.kind}:${link.url}`}
          title={project.description || link.url}
          aria-label={`${project.name} · ${link.label} · ${link.url}`}
        >
          {linkLabel(link.kind)}
        </a>
      ))}
    </div>
  );
}

function ProjectActions({
  project,
  disabled,
  onEdit,
  onUpdate,
}: {
  project: HostedProject;
  disabled: boolean;
  onEdit: () => void;
  onUpdate: UpdateProject;
}) {
  if (project.status === PROJECT_STATUS.HIDDEN) {
    return (
      <div className="project-directory__actions">
        <ActionButton
          label="Unhide"
          project={project}
          disabled={disabled}
          onClick={() =>
            void onUpdate(project, { status: PROJECT_STATUS.LISTED }, `Unhidden ${project.name}`)
          }
        />
      </div>
    );
  }

  return (
    <div className="project-directory__actions">
      {project.status === PROJECT_STATUS.NEW && (
        <ActionButton
          label="Keep"
          project={project}
          disabled={disabled}
          onClick={() =>
            void onUpdate(project, { status: PROJECT_STATUS.LISTED }, `Kept ${project.name}`)
          }
        />
      )}
      <ActionButton label="Edit" project={project} disabled={disabled} onClick={onEdit} />
      <ActionButton
        label="Hide"
        project={project}
        disabled={disabled}
        onClick={() =>
          void onUpdate(project, { status: PROJECT_STATUS.HIDDEN }, `Hidden ${project.name}`)
        }
      />
    </div>
  );
}

function ActionButton({
  label,
  project,
  disabled,
  onClick,
}: {
  label: string;
  project: HostedProject;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="secondary-button"
      disabled={disabled}
      aria-label={`${label} ${project.name}`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}
