import { useState, type ReactNode } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { BackupPriorityControls } from './BackupPriorityControls';
import { BackupPreferenceSections } from './BackupPreferenceSections';
import { useBackupPreferences, type PreferencesEditor } from './useBackupPreferences';
import './settings.css';

interface PanelProps {
  refresh: () => Promise<unknown>;
  capacityControl: ReactNode;
}

export function BackupSettingsPanel(props: PanelProps) {
  const [open, setOpen] = useState(false);
  const editor = useBackupPreferences(open, props.refresh);
  return (
    <>
      <button
        type="button"
        className="icon-button"
        aria-label="Backup settings"
        title="Backup settings"
        onClick={() => setOpen(true)}
      >
        ⚙
      </button>
      {open && <PreferencesDialog {...props} editor={editor} onClose={() => setOpen(false)} />}
    </>
  );
}

function PreferencesDialog({
  onClose,
  refresh,
  capacityControl,
  editor,
}: PanelProps & { editor: PreferencesEditor; onClose: () => void }) {
  const [group, setGroup] = useState('pi-cloud');
  return (
    <BottomSheet
      open
      title="Backup settings"
      className="backup-settings-sheet"
      onClose={onClose}
      headerActions={editor.busy && <span role="status">Applying…</span>}
    >
      <div className="backup-settings-form" aria-busy={editor.busy}>
        <div className="backup-settings-layout">
          <PreferenceNavigation editor={editor} group={group} select={setGroup} />
          <div className="backup-settings-content">
            {editor.error && (
              <div>
                <p role="alert" className="error-message">
                  {editor.error}
                </p>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={editor.busy}
                  onClick={() => void editor.reload()}
                >
                  {editor.count ? 'Reload & discard edits' : 'Reload settings'}
                </button>
              </div>
            )}
            {!editor.settings && !editor.error && <p role="status">Loading settings…</p>}
            {group === 'priority' ? (
              <BackupPriorityControls
                busy={editor.busy}
                runAction={editor.runAction}
                refresh={refresh}
              />
            ) : (
              <BackupPreferenceSections
                editor={editor}
                group={group}
                capacityControl={capacityControl}
              />
            )}
          </div>
        </div>
      </div>
    </BottomSheet>
  );
}

function PreferenceNavigation({
  editor,
  group,
  select,
}: {
  editor: PreferencesEditor;
  group: string;
  select: (group: string) => void;
}) {
  return (
    <nav className="backup-settings-nav" aria-label="Backup setting categories">
      {(editor.settings?.groups ?? []).map((item) => (
        <button
          key={item.id}
          type="button"
          aria-pressed={group === item.id}
          title={
            editor.settings?.fields.some(
              (field) => field.group === item.id && Object.hasOwn(editor.fieldErrors, field.id),
            )
              ? 'Check the highlighted values in this category.'
              : undefined
          }
          onClick={() => select(item.id)}
        >
          {item.label}
          <span aria-hidden="true">
            {editor.settings?.fields.some(
              (field) => field.group === item.id && Object.hasOwn(editor.fieldErrors, field.id),
            )
              ? '!'
              : ''}
          </span>
        </button>
      ))}
      <button type="button" aria-pressed={group === 'priority'} onClick={() => select('priority')}>
        Priority &amp; capture
      </button>
    </nav>
  );
}
