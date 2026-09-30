import { useState } from 'react';

import { StatusPill } from '../../components/StatusPill';
import type {
  HistoryQuery,
  NetworkCoverage,
  NetworkEvent,
  NetworkHistoryReport,
  NetworkIncident,
  NetworkIncidentDetail,
} from './types';

function StorageWarning({ coverage }: { coverage: NetworkCoverage[] }) {
  const storage = coverage.find(
    (source) => source.source === 'storage' && source.status !== 'current',
  );
  if (!storage) return null;
  return (
    <p className="network-history-warning" role="status" aria-label="Recorder storage warning">
      <strong>Recorder storage warning.</strong> {storage.detail}
    </p>
  );
}

export function utcTime(timestamp: number | null | undefined): string {
  return timestamp === null || timestamp === undefined
    ? 'Unknown'
    : new Date(timestamp * 1000)
        .toISOString()
        .replace('T', ' ')
        .replace('.000Z', 'Z')
        .replace('Z', ' UTC');
}

function duration(incident: NetworkIncident): string {
  if (incident.end === null)
    return incident.status === 'ongoing' ? 'Ongoing at last evidence' : 'End unknown';
  const seconds = Math.max(0, incident.end - incident.onset);
  return seconds < 60 ? `${Math.round(seconds)}s` : `${(seconds / 60).toFixed(1)} min`;
}

export function Evidence({ event }: { event: NetworkEvent }) {
  return (
    <details className="network-history-evidence">
      <summary>
        <time>{utcTime(event.time)}</time>
        <span>{event.message}</span>
        <small>
          {event.device} · {event.uplink || 'device'} · {event.kind} · {event.time_quality}
        </small>
      </summary>
      <dl className="network-history-provenance">
        <dt>Database row ID</dt>
        <dd>{event.id}</dd>
        {event.record_id && (
          <>
            <dt>Record identity</dt>
            <dd>{event.record_id}</dd>
          </>
        )}
        <dt>Source</dt>
        <dd>{event.source}</dd>
        <dt>Device source time</dt>
        <dd>{utcTime(event.source_time)}</dd>
        <dt>Receiver time</dt>
        <dd>{utcTime(event.received_at)}</dd>
        <dt>Imported into recorder</dt>
        <dd>{utcTime(event.imported_at)}</dd>
        <dt>Time quality</dt>
        <dd>{event.time_quality}</dd>
        {event.state && (
          <>
            <dt>Observed state</dt>
            <dd>{event.state}</dd>
          </>
        )}
        {event.domain && (
          <>
            <dt>Evidence domain</dt>
            <dd>{event.domain}</dd>
          </>
        )}
        {event.stream && (
          <>
            <dt>Stream</dt>
            <dd>{event.stream}</dd>
          </>
        )}
        {event.boot_id && (
          <>
            <dt>Boot/session</dt>
            <dd>{event.boot_id}</dd>
          </>
        )}
        {event.monotonic != null && (
          <>
            <dt>Monotonic time</dt>
            <dd>{event.monotonic}</dd>
          </>
        )}
      </dl>
      <pre aria-label={`Provenance for evidence ${event.id}`}>
        {JSON.stringify(event.provenance, null, 2)}
      </pre>
    </details>
  );
}

export function IncidentSummary({ incident }: { incident: NetworkIncident }) {
  return (
    <>
      <strong>{incident.summary}</strong>
      <span>
        {utcTime(incident.onset)} · {duration(incident)}
      </span>
      <small>
        {incident.uplinks.join(', ') || 'Device/monitoring'} · {incident.domains.join(', ')} ·{' '}
        {incident.status}
      </small>
    </>
  );
}

export function IncidentDetail({ detail }: { detail: NetworkIncidentDetail }) {
  const incident = detail.incident;
  const download = () => {
    const objectUrl = URL.createObjectURL(
      new Blob([JSON.stringify(detail.raw, null, 2)], { type: 'application/json' }),
    );
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = `network-incident-${incident.id}.json`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  };
  return (
    <section className="network-history-detail" aria-label="Incident evidence">
      <h3>Incident evidence</h3>
      <StorageWarning coverage={detail.coverage} />
      <p>Evidence snapshot generated {utcTime(detail.generated_at)}.</p>
      <div className="network-history-incident-summary">
        <IncidentSummary incident={incident} />
      </div>
      <p>
        End: {utcTime(incident.end)}. Latest evidence: {utcTime(incident.latest_evidence_at)}.
      </p>
      <h4>Observations</h4>
      <ul>
        {incident.observations.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
      <h4>Hypotheses · not confirmed causes</h4>
      {incident.hypotheses.length ? (
        <ul>
          {incident.hypotheses.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      ) : (
        <p>Cause undetermined from the available observations.</p>
      )}
      <button type="button" className="secondary-button" onClick={download}>
        Download redacted bundle
      </button>
      <p>
        {detail.events.length} of {detail.counts.events} observations and nearby context shown.
        Schema {detail.schema_version}.
      </p>
      {detail.events.length < detail.counts.events && (
        <p className="network-history-warning">
          Evidence is bounded; the bundle does not contain every observation.
        </p>
      )}
      {detail.events.map((event) => (
        <Evidence key={event.id} event={event} />
      ))}
      <details>
        <summary>Bundle coverage and timing</summary>
        <pre>
          {JSON.stringify(
            { coverage: detail.coverage, time_semantics: detail.time_semantics },
            null,
            2,
          )}
        </pre>
      </details>
      <ul className="network-history-limitations">
        {detail.limitations.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
    </section>
  );
}

interface FilterProps {
  query: HistoryQuery;
  report: NetworkHistoryReport | null;
  onApply: (query: HistoryQuery) => void;
}

export function HistoryFilters({ query, report, onApply }: FilterProps) {
  const [range, setRange] = useState(query.hours ? String(query.hours) : 'custom');
  const [start, setStart] = useState(
    new Date((query.start ?? Date.now() / 1000 - 6 * 3600) * 1000).toISOString().slice(0, 16),
  );
  const [end, setEnd] = useState(
    new Date((query.end ?? Date.now() / 1000) * 1000).toISOString().slice(0, 16),
  );
  const [uplink, setUplink] = useState(query.uplink ?? '');
  const [device, setDevice] = useState(query.device ?? '');
  const [search, setSearch] = useState(query.search ?? '');
  const [error, setError] = useState('');
  const uplinks = [...new Set([...(report?.filters.uplinks ?? []), ...(uplink ? [uplink] : [])])];
  const devices = [...new Set([...(report?.filters.devices ?? []), ...(device ? [device] : [])])];

  return (
    <form
      className="network-history-filters"
      onSubmit={(event) => {
        event.preventDefault();
        const next: HistoryQuery = { uplink, device, search: search.trim() };
        if (range === 'custom') {
          next.start = new Date(`${start}Z`).getTime() / 1000;
          next.end = new Date(`${end}Z`).getTime() / 1000;
          if (
            !Number.isFinite(next.start) ||
            !Number.isFinite(next.end) ||
            next.end <= next.start ||
            next.end - next.start > 720 * 3600
          ) {
            setError('Choose an increasing UTC interval of at most 30 days.');
            return;
          }
        } else next.hours = Number(range);
        setError('');
        onApply(next);
      }}
    >
      <label>
        Time range
        <select value={range} onChange={(event) => setRange(event.target.value)}>
          <option value="1">Last hour</option>
          <option value="6">Last 6 hours</option>
          <option value="24">Last 24 hours</option>
          <option value="168">Last 7 days</option>
          <option value="720">Last 30 days</option>
          <option value="custom">Custom UTC interval</option>
        </select>
      </label>
      {range === 'custom' && (
        <>
          <label>
            Start (UTC)
            <input
              type="datetime-local"
              value={start}
              onChange={(event) => setStart(event.target.value)}
              required
            />
          </label>
          <label>
            End (UTC)
            <input
              type="datetime-local"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
              required
            />
          </label>
        </>
      )}
      <label>
        Uplink
        <select value={uplink} onChange={(event) => setUplink(event.target.value)}>
          <option value="">All uplinks</option>
          {uplinks.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
      </label>
      <label>
        Device
        <select value={device} onChange={(event) => setDevice(event.target.value)}>
          <option value="">All devices</option>
          {devices.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
      </label>
      <label className="network-history-search">
        Search evidence
        <input
          type="search"
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Probe, DHCP, authentication…"
        />
      </label>
      <button type="submit" className="secondary-button">
        Apply filters
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}

export function HistoryReport({
  report,
  onIncident,
}: {
  report: NetworkHistoryReport;
  onIncident: (id: string) => void;
}) {
  return (
    <>
      <StorageWarning coverage={report.coverage} />
      <p className="network-history-range">
        {utcTime(report.range.start)} → {utcTime(report.range.end)}
        <br />
        {report.counts.events} observations · {report.counts.incidents} incidents · generated{' '}
        {utcTime(report.generated_at)}
      </p>
      <section aria-label="Source coverage" className="network-history-coverage">
        <h3>Source coverage</h3>
        <p>
          Freshness describes collection, not internet health. Missing observations do not prove a
          connection was healthy.
        </p>
        {report.coverage.length === 0 && <p>No source coverage is available.</p>}
        {report.coverage.map((source, index) => (
          <details key={`${source.source}:${source.device}:${index}`}>
            <summary>
              <strong>{source.source}</strong>
              <span>{source.device}</span>
              <StatusPill tone={source.status === 'current' ? 'neutral' : 'warning'}>
                {source.status}
              </StatusPill>
            </summary>
            <p>{source.detail}</p>
            <p>
              Last event: {utcTime(source.last_event_at)}
              <br />
              Last receipt: {utcTime(source.last_received_at)}
            </p>
          </details>
        ))}
      </section>
      <section aria-label="Network incidents">
        <h3>Incidents</h3>
        {report.incidents.length === 0 && (
          <p>
            No matching incidents in the retained evidence. This does not establish uninterrupted
            connectivity.
          </p>
        )}
        {report.incidents.map((incident) => (
          <button
            key={incident.id}
            type="button"
            className="network-history-incident"
            onClick={() => onIncident(incident.id)}
          >
            <IncidentSummary incident={incident} />
            <span className="network-history-evidence-link">View supporting evidence →</span>
          </button>
        ))}
        {report.incidents.length < report.counts.incidents && (
          <p className="network-history-warning">
            Showing {report.incidents.length} of {report.counts.incidents} incidents. Narrow the
            interval or filters to inspect the rest.
          </p>
        )}
      </section>
      <section aria-label="Combined network timeline">
        <h3>Combined timeline</h3>
        <p>
          Times are UTC. Receiver times and uncertain device clocks cannot establish exact
          cross-device order. Expand an observation for provenance.
        </p>
        {report.events.length === 0 && (
          <p>No observations match this interval and these filters.</p>
        )}
        {report.events.length < report.counts.events && (
          <p className="network-history-warning">
            Showing {report.events.length} of {report.counts.events} observations. Counts cover the
            full selected interval; narrow the range to see other evidence.
          </p>
        )}
        {report.events.map((event) => (
          <Evidence key={event.id} event={event} />
        ))}
      </section>
      <details className="network-history-limitations">
        <summary>Timing semantics and limitations</summary>
        <pre>{JSON.stringify(report.time_semantics, null, 2)}</pre>
        <ul>
          {report.limitations.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      </details>
    </>
  );
}
