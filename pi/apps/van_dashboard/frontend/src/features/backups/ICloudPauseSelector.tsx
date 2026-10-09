import { INDEFINITE_PAUSE } from './icloud';

interface ICloudPauseSelectorProps {
  onPause: (minutes: string) => void;
  disabled: boolean;
}

export function ICloudPauseSelector({ onPause, disabled }: ICloudPauseSelectorProps) {
  return (
    <div className="backup-pause" role="group" aria-label="Pause backup">
      <button
        type="button"
        className="secondary-button backup-pause__action"
        title="Pause indefinitely"
        disabled={disabled}
        onClick={() => onPause(INDEFINITE_PAUSE)}
      >
        Pause
      </button>
      <span
        className="secondary-button backup-pause__duration"
        data-disabled={disabled || undefined}
      >
        <span aria-hidden="true">▾</span>
        <select
          aria-label="Pause duration"
          title="Pause for a duration"
          value=""
          onChange={(event) => onPause(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.preventDefault();
              event.stopPropagation();
            }
          }}
          disabled={disabled}
        >
          <option value="" disabled>
            Pause for…
          </option>
          <option value="15">15 minutes</option>
          <option value="30">30 minutes</option>
          <option value="60">1 hour</option>
          <option value="240">4 hours</option>
          <option value="720">12 hours</option>
          <option value="1440">24 hours</option>
          <option value="10080">7 days</option>
        </select>
      </span>
    </div>
  );
}
