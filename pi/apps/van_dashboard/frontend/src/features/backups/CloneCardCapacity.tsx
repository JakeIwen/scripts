import type { BackupSettings } from './types';
import type { BackupControls } from './controls';

export function CloneCardCapacity({
  settings,
  controls,
}: {
  settings: BackupSettings;
  controls: BackupControls;
}) {
  return (
    <div className="backup-capacity-control">
      <label>
        <span>Clone card size</span>
        <select
          aria-label="Bootable clone card capacity"
          value={settings.cloneCardNominalGb}
          disabled={controls.blocked}
          onChange={(event) => {
            const selected = settings.cloneCardNominalGbOptions.find(
              (option) => String(option) === event.currentTarget.value,
            );
            if (selected !== undefined) void controls.setCloneCardNominalGb(selected);
          }}
        >
          {settings.cloneCardNominalGbOptions.map((option) => (
            <option value={option} key={option}>
              {option} GB
            </option>
          ))}
        </select>
      </label>
      <small title="Root usage above this level may no longer fit safely on the configured cards.">
        Root limit · {settings.rootUsedMaxGib} GiB
      </small>
      {controls.lastError && (
        <p role="alert" className="error-message">
          {controls.lastError}
        </p>
      )}
    </div>
  );
}
