import { consumerLabels, isGated } from './presentation';
import type { Consumer, HostState, PolicyEdit } from './schema';

interface MeteredConsumersProps {
  consumers: Consumer[];
  state: HostState;
  stale?: boolean;
  blocked?: boolean;
  onChange?: (edit: PolicyEdit) => Promise<boolean>;
}

function ApprovalMenu({
  consumer,
  blocked,
  onChange,
}: {
  consumer: Consumer;
  blocked: boolean;
  onChange: (edit: PolicyEdit) => Promise<boolean>;
}) {
  return (
    <div className="metered-actions" role="group" aria-label={`${consumer.label} permission`}>
      <select
        aria-label={`Approve ${consumer.label}`}
        value=""
        disabled={blocked}
        onChange={(event) => {
          const value = event.currentTarget.value;
          void onChange(
            value === 'episode'
              ? { op: 'approve', consumer: consumer.id, value: 'episode' }
              : { op: 'approve', consumer: consumer.id, value: 'until', seconds: Number(value) },
          );
        }}
      >
        <option value="" disabled>
          Approve…
        </option>
        <option value="3600">1 h</option>
        <option value="14400">4 h</option>
        <option value="episode">This metered episode</option>
      </select>
      <button
        className="secondary-button"
        type="button"
        disabled={blocked}
        aria-label={`Revoke ${consumer.label}`}
        onClick={() => void onChange({ op: 'revoke', consumer: consumer.id })}
      >
        Revoke
      </button>
    </div>
  );
}

export function MeteredConsumers({
  consumers,
  state,
  stale = false,
  blocked = true,
  onChange,
}: MeteredConsumersProps) {
  return (
    <div className="metered-consumers">
      {consumers.map((consumer) => {
        const labels = consumerLabels(consumer, state, stale);
        const reason = state.consumers[consumer.id]?.reason;
        return (
          <article className="metered-consumer" key={consumer.id} aria-label={consumer.label}>
            <div className="metered-consumer__identity">
              <strong>{consumer.label}</strong>
              <details className="metered-info">
                <summary aria-label={`About ${consumer.label}`}>ⓘ</summary>
                <p>
                  {consumer.note}
                  {reason && ` ${reason}.`}
                </p>
              </details>
            </div>
            <dl className="metered-consumer__state">
              <div>
                <dt>Permission</dt>
                <dd>{labels.permission}</dd>
              </div>
              <div>
                <dt>Actual</dt>
                <dd>{labels.actual}</dd>
              </div>
            </dl>
            {consumer.how === 'unprotected' && (
              <small className="metered-note">{consumer.note}</small>
            )}
            {onChange && isGated(consumer) && (
              <ApprovalMenu consumer={consumer} blocked={blocked} onChange={onChange} />
            )}
          </article>
        );
      })}
    </div>
  );
}
