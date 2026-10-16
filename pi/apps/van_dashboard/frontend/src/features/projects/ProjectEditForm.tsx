import { useRef, useState } from 'react';

import type { ProjectPatch } from './api';
import type { HostedProject } from './schema';

export function ProjectEditForm({
  project,
  disabled,
  onSave,
  onCancel,
}: {
  project: HostedProject;
  disabled: boolean;
  onSave: (patch: ProjectPatch) => Promise<boolean>;
  onCancel: () => void;
}) {
  const inFlight = useRef(false);
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const unavailable = disabled || saving;

  return (
    <form
      className="project-directory__edit-form"
      onSubmit={async (event) => {
        event.preventDefault();
        if (inFlight.current || unavailable) return;
        inFlight.current = true;
        setSaving(true);
        try {
          const saved = await onSave({ name: name.trim(), description: description.trim() });
          if (saved) onCancel();
        } finally {
          inFlight.current = false;
          setSaving(false);
        }
      }}
    >
      <fieldset disabled={unavailable}>
        <label>
          Name
          <input
            name="name"
            required
            maxLength={80}
            autoComplete="off"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          Description
          <textarea
            name="description"
            rows={2}
            maxLength={200}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
      </fieldset>
      <div className="project-directory__edit-actions">
        <button
          type="submit"
          className="secondary-button"
          disabled={unavailable}
          aria-label={`Save changes to ${project.name}`}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          className="secondary-button"
          disabled={unavailable}
          aria-label={`Cancel editing ${project.name}`}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
