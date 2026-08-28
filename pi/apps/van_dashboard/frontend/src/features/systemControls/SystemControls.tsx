import { useToast } from '../../components/ToastProvider';
import { useSystemControls } from './controls';
import './systemControls.css';

export interface SystemControlsProps {
  disabled?: boolean;
}

export function SystemControls({ disabled = false }: SystemControlsProps) {
  const { showToast } = useToast();
  const controls = useSystemControls(showToast);
  const controlsDisabled = disabled || controls.locked;

  return (
    <div className="system-controls">
      <div className="system-controls__buttons" aria-label="Vanpi system controls">
        <button
          className="secondary-button"
          type="button"
          disabled={controlsDisabled}
          onClick={() => void controls.restartDashboard()}
        >
          Restart UI
        </button>
        <button
          className="danger-button"
          type="button"
          disabled={controlsDisabled}
          onClick={() => void controls.requestPower('reboot')}
        >
          Reboot
        </button>
        <button
          className="danger-button"
          type="button"
          disabled={controlsDisabled}
          onClick={() => void controls.requestPower('power-down')}
        >
          Power down
        </button>
      </div>
      <span
        className={`system-controls__status ${controls.error ? 'system-controls__status--error' : ''}`}
        aria-live="polite"
      >
        {controls.error ?? controls.message ?? 'Guarded system controls'}
      </span>
    </div>
  );
}
