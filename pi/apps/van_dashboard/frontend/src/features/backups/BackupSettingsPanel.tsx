import { useState, type ReactNode } from 'react';
import { BottomSheet } from '../../components/BottomSheet';
import { BackupPriorityControls } from './BackupPriorityControls';
import { BackupPreferenceSections, PreferenceFooter } from './BackupPreferenceSections';
import { useBackupPreferences, type PreferencesEditor } from './useBackupPreferences';
import './settings.css';

interface PanelProps {
  refresh: () => Promise<unknown>;
  capacityControl: ReactNode;
}

export function BackupSettingsPanel(props: PanelProps) {
  const [open, setOpen] = useState(false);
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
      {open && <PreferencesDialog {...props} onClose={() => setOpen(false)} />}
    </>
  );
}

function PreferencesDialog({
  onClose,
  refresh,
  capacityControl,
}: PanelProps & { onClose: () => void }) {
  const editor = useBackupPreferences(refresh, onClose);
  const [group, setGroup] = useState('pi-cloud');
  return (
    <BottomSheet
      open
      title="Backup settings"
      className="backup-settings-sheet"
      onClose={editor.close}
    >
      <form onSubmit={(event) => void editor.save(event)} className="backup-settings-form">
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
                setBusy={editor.setBusy}
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
        <PreferenceFooter editor={editor} onClose={onClose} />
      </form>
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
          disabled={editor.busy}
          aria-pressed={group === item.id}
          onClick={() => select(item.id)}
        >
          {item.label}
          <span aria-hidden="true">
            {editor.settings?.fields.some(
              (field) => field.group === item.id && field.id in editor.changes,
            )
              ? '•'
              : ''}
          </span>
        </button>
      ))}
      <button
        type="button"
        disabled={editor.busy}
        aria-pressed={group === 'priority'}
        onClick={() => select('priority')}
      >
        Priority &amp; capture
      </button>
    </nav>
  );
}
