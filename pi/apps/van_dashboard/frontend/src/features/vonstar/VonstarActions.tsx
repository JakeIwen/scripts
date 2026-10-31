import { VONSTAR_ACTION_NAMES, VONSTAR_ACTIONS, type VonstarActionName } from './types';

export function VonstarActions({
  disabled,
  onAction,
}: {
  disabled: boolean;
  onAction: (action: VonstarActionName) => void;
}) {
  return (
    <div className="vonstar-actions" role="group" aria-label="Vehicle lock controls">
      <button
        className="vonstar-action"
        type="button"
        disabled={disabled}
        onClick={() => onAction('lock_all')}
      >
        {VONSTAR_ACTIONS.lock_all.label}
      </button>
      <fieldset className="vonstar-unlock" disabled={disabled}>
        <legend>Unlock</legend>
        <div className="vonstar-unlock__buttons">
          {VONSTAR_ACTION_NAMES.filter((action) => action !== 'lock_all').map((action) => (
            <button
              key={action}
              className="vonstar-action"
              type="button"
              disabled={disabled}
              aria-label={VONSTAR_ACTIONS[action].label}
              onClick={() => onAction(action)}
            >
              {VONSTAR_ACTIONS[action].shortLabel}
            </button>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
