import { BottomSheet } from '../../components/BottomSheet';
import { StatusPill } from '../../components/StatusPill';
import {
  describeLockState,
  describeVonstar,
  driverDoorLabel,
  observedAtLabel,
  VONSTAR_ACCESS_CONFIRMATION,
} from './presentation';
import type { VonstarController } from './types';
import './vonstar.css';

export function VonstarSheet({
  open,
  onClose,
  controller,
}: {
  open: boolean;
  onClose: () => void;
  controller: VonstarController;
}) {
  const service = describeVonstar(controller.status, controller.busy);
  const lock = describeLockState(controller.accessState);
  const controlsEnabled =
    controller.status?.available === true &&
    controller.status.mode === 'execute' &&
    !controller.status.busy &&
    !controller.busy;
  const state = controller.accessState;

  const checkStatus = () => {
    if (!window.confirm(VONSTAR_ACCESS_CONFIRMATION)) return;
    void controller.checkAccessState();
  };

  return (
    <BottomSheet
      open={open}
      title="vOnStar status"
      description="Point-in-time lock and door evidence from one explicitly confirmed check."
      onClose={onClose}
    >
      <header className="vonstar-sheet-toolbar">
        <div>
          <strong>Guarded vehicle access</strong>
          <span>No automatic wake, retry, or live tracking.</span>
        </div>
        <StatusPill tone={service.tone}>{service.label}</StatusPill>
      </header>

      {controller.error && <p className="error-message">{controller.error.message}</p>}

      <div className="vonstar-access-overview">
        <section className={`vonstar-access-card vonstar-access-card--${lock.tone}`}>
          <span>Lock state</span>
          <strong>{lock.label}</strong>
          <small>{lock.detail}</small>
        </section>
        <section className="vonstar-access-card">
          <span>Door coverage</span>
          <strong>
            {state
              ? state.complete
                ? 'Complete door coverage'
                : 'Partial door coverage'
              : 'Not checked this session'}
          </strong>
          <small>
            {state ? `Last checked · ${observedAtLabel(state.observedAt)}` : 'Last checked · never'}
          </small>
        </section>
      </div>

      <dl className="vonstar-door-list" aria-label="Door state coverage">
        <div>
          <dt>Driver</dt>
          <dd>{driverDoorLabel(state)}</dd>
        </div>
        <div>
          <dt>Sliding</dt>
          <dd>
            {state ? 'Sliding door sensor bypassed — physical state unavailable' : 'Not checked'}
          </dd>
        </div>
        <div>
          <dt>Passenger / rear</dt>
          <dd>{state ? 'Passenger and rear ajar not mapped' : 'Not mapped'}</dd>
        </div>
      </dl>

      <button
        className="primary-button vonstar-check-state"
        type="button"
        disabled={!controlsEnabled || !open}
        aria-busy={controller.busy}
        onClick={checkStatus}
      >
        {controller.busy ? 'Checking…' : 'Check Status'}
      </button>

      <details className="vonstar-limitations">
        <summary>Coverage notes</summary>
        <ul>
          {(state?.limitations.length ? state.limitations : ['No state checked this session.']).map(
            (limitation, index) => (
              <li key={`${index}-${limitation}`}>{limitation}</li>
            ),
          )}
        </ul>
      </details>
    </BottomSheet>
  );
}
