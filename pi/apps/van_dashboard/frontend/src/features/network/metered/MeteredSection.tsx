import { StatusPill } from '../../../components/StatusPill';
import { formatRelativeTime } from '../../../utils/format';
import { MeteredAdvanced } from './MeteredAdvanced';
import { MeteredConsumers } from './MeteredConsumers';
import { MeteredSSIDs } from './MeteredSSIDs';
import { stateIsStale } from './presentation';
import type { HostState } from './schema';
import { useMetered } from './useMetered';
import './metered.css';

function VerdictLine({ state, stale = false }: { state: HostState; stale?: boolean }) {
  const metered = stale ? null : state.verdict.metered;
  return (
    <div className="metered-verdict">
      <StatusPill tone={metered === null ? 'neutral' : metered ? 'warning' : 'good'}>
        {metered === null ? 'Unknown' : metered ? 'Metered' : 'Unmetered'}
      </StatusPill>
      <span>{stale ? 'Evaluation is stale or awaiting reconciliation' : state.verdict.reason}</span>
      {state.verdict.matched.map((match) => (
        <code key={`${match.member}:${match.ssid}`}>
          {match.member} · {match.ssid}
        </code>
      ))}
      {state.effective.source === 'mode' && (
        <small>Mode override: {state.effective.metered ? 'metered' : 'unmetered'}</small>
      )}
    </div>
  );
}

export function MeteredSection({ open }: { open: boolean }) {
  const controls = useMetered(open);
  const { resource } = controls;
  const status = resource.data;
  const stale = status
    ? stateIsStale(status.state) ||
      status.state.config_revision !== status.config.revision ||
      status.state.policy_revision !== status.policy.revision
    : false;
  return (
    <section className="metered-section" aria-labelledby="metered-title">
      <header className="metered-header">
        <h3 id="metered-title">Metered</h3>
        <button
          type="button"
          className="secondary-button"
          disabled={!open || resource.refreshing}
          onClick={() => void resource.refresh()}
        >
          Refresh metering
        </button>
      </header>
      {resource.error && (
        <p className="error-message" role="alert">
          {resource.error.message}
        </p>
      )}
      {controls.notice && (
        <p role="status" className={controls.failed ? 'error-message' : 'metered-note'}>
          {controls.notice}
        </p>
      )}
      {!status ? (
        <p className="metered-note">
          {resource.error ? 'Metering status unavailable' : 'Loading metering…'}
        </p>
      ) : (
        <>
          <VerdictLine state={status.state} stale={stale} />
          <small className="metered-note">
            Pi evaluated {formatRelativeTime(status.state.evaluated_at)}
          </small>
          <MeteredSSIDs ssids={status.config.metered_ssids} controls={controls} />
          <h4>Pi consumers</h4>
          <MeteredConsumers
            consumers={status.registry.filter((row) => row.host === 'pi')}
            state={status.state}
            stale={stale}
            blocked={controls.blocked}
            onChange={controls.changePolicy}
          />
          <MeteredAdvanced controls={controls} status={status} />
          <div className="metered-mac">
            <h4>Mac · last report</h4>
            <p className="metered-note">Change these on the Mac Panel</p>
            {status.mac_report === null ? (
              <p>No Mac report received yet.</p>
            ) : (
              <>
                <p className="metered-note">
                  Reported {formatRelativeTime(status.mac_report.evaluated_at)}
                  {stateIsStale(status.mac_report) ? ' · stale' : ''}
                </p>
                <VerdictLine state={status.mac_report} />
                <MeteredConsumers
                  consumers={status.registry.filter((row) => row.host === 'mac')}
                  state={status.mac_report}
                />
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}
