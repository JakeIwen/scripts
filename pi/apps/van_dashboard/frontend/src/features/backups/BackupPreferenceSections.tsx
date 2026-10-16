import type { ReactNode } from 'react';
import { BackupSettingField, SettingHelp } from './BackupSettingField';
import type { PreferencesEditor } from './useBackupPreferences';

export function BackupPreferenceSections({
  editor,
  group,
  capacityControl,
}: {
  editor: PreferencesEditor;
  group: string;
  capacityControl: ReactNode;
}) {
  const fields = editor.settings?.fields.filter((field) => field.group === group) ?? [];
  const sections = [...new Set(fields.map((field) => field.section))];
  return (
    <>
      <header className="backup-settings-content__header">
        <h3>{editor.settings?.groups.find((item) => item.id === group)?.label}</h3>
        {group === 'disk' && (
          <span
            className="backup-settings-chip"
            title="Daily retry windows use the Pi’s local time, which may differ from this browser."
          >
            Pi time · {editor.settings?.timezone}
          </span>
        )}
        {group.endsWith('-cloud') && (
          <span
            className="backup-settings-chip"
            title="Uploads require permitted non-Starlink internet, safe mounted storage and ignition off."
          >
            Starlink excluded
          </span>
        )}
      </header>
      {sections.map((section) => (
        <section key={section} className="backup-settings-section">
          <h4>{section}</h4>
          <div
            className={`backup-setting-grid${fields.filter((field) => field.section === section).length === 3 ? ' backup-setting-grid--three' : ''}`}
          >
            {fields
              .filter((field) => field.section === section)
              .map((field) => (
                <BackupSettingField
                  key={field.id}
                  field={field}
                  value={editor.draft[field.id] ?? ''}
                  disabled={editor.busy}
                  error={editor.fieldErrors[field.id]}
                  onChange={(value) => editor.change(field.id, value)}
                />
              ))}
          </div>
        </section>
      ))}
      {group === 'hotspares' && (
        <section className="backup-settings-section">
          <div className="backup-settings-section__title">
            <h4>Card capacity</h4>
            <SettingHelp label="Card capacity">
              Nominal size of the boot and spare cards. Applies immediately; the root-usage warning
              reserves the same filesystem headroom.
            </SettingHelp>
          </div>
          {capacityControl}
        </section>
      )}
    </>
  );
}

export function PreferenceFooter({
  editor,
  onClose,
}: {
  editor: PreferencesEditor;
  onClose: () => void;
}) {
  const invalid = Object.keys(editor.fieldErrors).length;
  return (
    <footer className="backup-settings-footer">
      {editor.discard ? (
        <>
          <span>
            Discard {editor.count} unsaved {editor.count === 1 ? 'change' : 'changes'}?
          </span>
          <button type="button" className="secondary-button" onClick={editor.keepEditing}>
            Keep editing
          </button>
          <button type="button" className="primary-button" onClick={onClose}>
            Discard &amp; close
          </button>
        </>
      ) : (
        <>
          <span
            role="status"
            title={invalid ? Object.values(editor.fieldErrors).join('\n') : undefined}
          >
            {editor.busy
              ? 'Applying…'
              : invalid
                ? `Check ${invalid} ${invalid === 1 ? 'value' : 'values'}`
                : editor.count
                  ? `${editor.count} unsaved ${editor.count === 1 ? 'change' : 'changes'}`
                  : editor.saved
                    ? 'Saved · applies to the next run'
                    : 'Changes apply to the next run'}
          </span>
          <button
            type="button"
            className="secondary-button"
            disabled={editor.busy || !editor.count}
            onClick={editor.cancel}
          >
            Cancel changes
          </button>
          <button type="submit" className="primary-button" disabled={!editor.canSave}>
            Save changes
          </button>
        </>
      )}
    </footer>
  );
}
