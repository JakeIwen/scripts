import { useToast } from '../../components/ToastProvider';
import { useSystemControls } from './controls';
import './systemControls.css';

export interface SystemControlsProps {
  disabled?: boolean;
  editing?: boolean;
  onEditingChange?: (editing: boolean) => void;
}

export function SystemControls({
  disabled = false,
  editing = false,
  onEditingChange = () => {},
}: SystemControlsProps) {
  const { showToast } = useToast();
  const controls = useSystemControls(showToast);
  const controlsDisabled = disabled || controls.locked;

  return (
    <div className="system-controls">
      <div className="system-controls__rows" aria-label="Vanpi system controls">
        <div className="system-controls__row">
          <span className="system-controls__label">Pi</span>
          <button
            className="system-controls__icon"
            type="button"
            disabled={controlsDisabled}
            onClick={() => void controls.requestPower('reboot')}
            title="Safely reboot vanpi"
            aria-label="Restart Pi"
          >
            <span aria-hidden="true">↻</span>
          </button>
          <button
            className="system-controls__icon system-controls__power"
            type="button"
            disabled={controlsDisabled}
            onClick={() => void controls.requestPower('power-down')}
            title="Safely power down vanpi"
            aria-label="Power down Pi"
          >
            <span aria-hidden="true">⏻</span>
          </button>
        </div>
        <div className="system-controls__row">
          <span className="system-controls__label">Dash</span>
          <button
            className="system-controls__icon"
            type="button"
            disabled={controlsDisabled}
            onClick={() => void controls.restartDashboard()}
            title="Restart only the dashboard service"
            aria-label="Restart dashboard"
          >
            <span aria-hidden="true">↻</span>
          </button>
          <button
            className={`system-controls__icon system-controls__edit ${editing ? 'is-active' : ''}`}
            type="button"
            aria-pressed={editing}
            onClick={() => onEditingChange(!editing)}
            title={editing ? 'Finish arranging tiles' : 'Arrange tiles'}
            aria-label={editing ? 'Finish arranging tiles' : 'Arrange tiles'}
          >
            <span aria-hidden="true">{editing ? '✓' : '✎'}</span>
          </button>
        </div>
      </div>
      <span className="visually-hidden" role="status" aria-live="polite">
        {controls.error ?? controls.message ?? ''}
      </span>
    </div>
  );
}
