import { useRef, useState } from 'react';

import { useToast } from '../../components/ToastProvider';
import {
  addHostedProject,
  fetchHostedProjects,
  PROJECT_URL_FIELDS,
  type ProjectDraft,
} from './api';
import type { HostedProject } from './catalog';

export function NewProjectForm({
  projects,
  disabled,
  onSaved,
}: {
  projects: HostedProject[];
  disabled: boolean;
  onSaved: (projects: HostedProject[]) => void;
}) {
  const details = useRef<HTMLDetailsElement>(null);
  const inFlight = useRef(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const { showToast } = useToast();

  return (
    <details className="project-directory__new" ref={details}>
      <summary>New Hosted Project</summary>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (inFlight.current || disabled) return;
          const form = event.currentTarget;
          const values = new FormData(form);
          const draft: ProjectDraft = { name: '', local: '', lan: '', ts: '', web: '' };
          for (const key of Object.keys(draft) as (keyof ProjectDraft)[]) {
            draft[key] = String(values.get(key) ?? '').trim();
          }
          if (
            projects.some(
              (project) => project.name.toLocaleLowerCase() === draft.name.toLocaleLowerCase(),
            )
          ) {
            setError('A project with that name already exists');
            return;
          }
          if (!PROJECT_URL_FIELDS.some(({ kind }) => draft[kind])) {
            setError('Enter at least one URL');
            return;
          }
          inFlight.current = true;
          setSaving(true);
          setError('');
          try {
            onSaved(await addHostedProject(draft));
            form.reset();
            if (details.current) details.current.open = false;
            showToast(`Added ${draft.name}`);
          } catch (reason) {
            setError(reason instanceof Error ? reason.message : 'Could not save project');
            // A lost response can follow a successful write. Reconcile the list
            // without retrying the mutation or clearing the user's draft.
            try {
              onSaved(await fetchHostedProjects(new AbortController().signal));
            } catch {
              // Preserve the original save error if the follow-up read also fails.
            }
          } finally {
            inFlight.current = false;
            setSaving(false);
          }
        }}
      >
        <fieldset disabled={disabled || saving}>
          <label>
            Name
            <input name="name" required maxLength={80} autoComplete="off" />
          </label>
          {PROJECT_URL_FIELDS.map(({ kind, label, placeholder }) => (
            <label key={kind}>
              {label} URL
              <input
                name={kind}
                type="url"
                maxLength={2048}
                placeholder={placeholder}
                autoCapitalize="none"
                spellCheck={false}
              />
            </label>
          ))}
          <small>Enter one or more addresses. Saved on vanpi for all dashboard devices.</small>
          <button type="submit" className="secondary-button">
            {saving ? 'Saving…' : 'Add project'}
          </button>
        </fieldset>
        {error && (
          <p className="error-message" role="alert">
            {error}
          </p>
        )}
      </form>
    </details>
  );
}
