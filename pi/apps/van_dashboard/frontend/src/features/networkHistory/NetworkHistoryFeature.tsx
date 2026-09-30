import { useCallback, useEffect, useRef, useState } from 'react';

import { BottomSheet } from '../../components/BottomSheet';
import { StatusPill } from '../../components/StatusPill';
import { Tile } from '../../components/Tile';
import { usePollingResource } from '../../hooks/usePollingResource';
import { fetchNetworkHistory, fetchNetworkIncident, historyQueryString } from './api';
import { HistoryFilters, HistoryReport, IncidentDetail } from './NetworkHistoryView';
import type { HistoryQuery, NetworkIncidentDetail } from './types';
import './networkHistory.css';

function readLink(): { open: boolean; query: HistoryQuery; incident: string | null } {
  const [path, raw] = window.location.hash.slice(1).split('?');
  if (path !== 'network-history') return { open: false, query: { hours: 6 }, incident: null };
  const parameters = new URLSearchParams(raw);
  const start = Number(parameters.get('start'));
  const end = Number(parameters.get('end'));
  const hours = Number(parameters.get('hours'));
  const query: HistoryQuery =
    start > 0 && end > start && end <= 253402300799 && end - start <= 720 * 3600
      ? { start, end }
      : { hours: [1, 6, 24, 168, 720].includes(hours) ? hours : 6 };
  for (const key of ['uplink', 'device', 'search'] as const) {
    const value = parameters.get(key);
    if (value) query[key] = value.slice(0, key === 'search' ? 200 : 128);
  }
  const incident = parameters.get('incident');
  return {
    open: true,
    query,
    incident: incident && /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(incident) ? incident : null,
  };
}

function writeLink(open: boolean, query: HistoryQuery, incident: string | null) {
  const url = new URL(window.location.href);
  const parameters = new URLSearchParams(historyQueryString(query));
  if (incident) parameters.set('incident', incident);
  url.hash = open ? `network-history?${parameters}` : '';
  window.history.replaceState(null, '', url);
}

export function NetworkHistoryFeature() {
  const [initial] = useState(readLink);
  const [open, setOpen] = useState(initial.open);
  const [query, setQuery] = useState<HistoryQuery>(initial.query);
  const [selected, setSelected] = useState<string | null>(initial.incident);
  const [detail, setDetail] = useState<NetworkIncidentDetail | null>(null);
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [now, setNow] = useState(Date.now);
  const detailRef = useRef<HTMLDivElement>(null);
  const scrolledIncident = useRef<string | null>(null);
  const queryKey = historyQueryString(query);
  const load = useCallback(
    async (signal: AbortSignal) => ({ report: await fetchNetworkHistory(query, signal), queryKey }),
    [query, queryKey],
  );
  const resource = usePollingResource({ load, intervalMs: open ? 30_000 : 60_000 });
  const report = resource.data?.report ?? null;
  const selectedRangeReady = resource.data?.queryKey === queryKey;
  const stale =
    Boolean(resource.error) ||
    (report !== null && now / 1000 - report.generated_at > 90) ||
    (resource.lastUpdatedAt !== null && now - resource.lastUpdatedAt > 90_000);
  const missingCoverage =
    !report ||
    report.coverage.length === 0 ||
    report.coverage.some((source) => source.status !== 'current');
  const storageWarning = report?.coverage.some(
    (source) => source.source === 'storage' && source.status !== 'current',
  );
  const status = resource.error
    ? 'Unavailable'
    : stale
      ? 'Stale report'
      : storageWarning
        ? 'Storage warning'
        : missingCoverage
          ? 'Partial coverage'
          : 'Recording';

  useEffect(() => {
    // A hanging HTTP request must not freeze an old Recording badge forever.
    // Age locally received data independently of polling completion.
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const update = () => {
      const next = readLink();
      setOpen(next.open);
      setQuery(next.query);
      setSelected(next.incident);
    };
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);

  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    scrolledIncident.current = null;
  }, [open, selected]);

  useEffect(() => {
    if (!open || !selected) return;
    const controller = new AbortController();
    void fetchNetworkIncident(selected, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) {
          setDetail(next);
          setDetailError(null);
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setDetailError(
            reason instanceof Error ? reason : new Error('Incident evidence unavailable'),
          );
      });
    return () => controller.abort();
  }, [open, selected, resource.lastUpdatedAt]);

  useEffect(() => {
    if (detail && scrolledIncident.current !== detail.incident.id) {
      detailRef.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' });
      scrolledIncident.current = detail.incident.id;
    }
  }, [detail]);

  const close = () => {
    setOpen(false);
    writeLink(false, query, selected);
  };
  const selectIncident = (id: string) => {
    setSelected(id);
    writeLink(true, query, id);
  };

  return (
    <>
      <Tile
        icon="◷"
        title="Network History"
        ariaLabel="Open network history"
        tone={stale || missingCoverage ? 'warning' : 'neutral'}
        status={
          <StatusPill tone={stale || missingCoverage ? 'warning' : 'neutral'}>
            {report || resource.error ? status : 'Loading'}
          </StatusPill>
        }
        summary={
          report
            ? `${report.counts.incidents} incidents · ${report.counts.events} observations`
            : resource.error
              ? 'Recorder evidence unavailable'
              : 'Loading retained network evidence…'
        }
        onClick={() => {
          setOpen(true);
          writeLink(true, query, selected);
        }}
      >
        <p className="network-history-tile-note">
          Disconnects, supporting evidence, and coverage gaps
        </p>
      </Tile>
      <BottomSheet
        open={open}
        title="Network history"
        description="Passive flight recorder · local reports · all timeline times UTC"
        onClose={close}
      >
        <div className="network-history">
          <HistoryFilters
            key={queryKey}
            query={query}
            report={report}
            onApply={(next) => {
              setQuery(next);
              setSelected(null);
              writeLink(true, next, null);
            }}
          />
          <div className="network-history-toolbar">
            <button
              type="button"
              className="secondary-button"
              onClick={() => void resource.refresh()}
              disabled={resource.refreshing}
            >
              {resource.refreshing ? 'Refreshing…' : 'Refresh evidence'}
            </button>
            <span>Share this view using the address bar link.</span>
          </div>
          {resource.error && (
            <p role="alert" className="network-history-warning">
              Evidence unavailable: {resource.error.message}.{' '}
              {report
                ? 'The last successful report remains visible and may be stale.'
                : 'Check the recorder service on the Pi.'}
            </p>
          )}
          {stale && !resource.error && (
            <p className="network-history-warning">
              This report is stale. Its old observations do not establish current network health.
            </p>
          )}
          {!selectedRangeReady && (
            <p role="status">
              {resource.error
                ? 'No report is available for the selected filters.'
                : 'Loading the selected interval and filters…'}
            </p>
          )}
          {selected && (
            <>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setSelected(null);
                  writeLink(true, query, null);
                }}
              >
                Close incident evidence
              </button>
              {detailError && (
                <p role="alert">Incident evidence unavailable: {detailError.message}</p>
              )}
              {!detail && !detailError && <p role="status">Loading incident evidence…</p>}
              {detail && (
                <div ref={detailRef}>
                  <IncidentDetail detail={detail} />
                </div>
              )}
            </>
          )}
          {report && selectedRangeReady && (
            <HistoryReport report={report} onIncident={selectIncident} />
          )}
        </div>
      </BottomSheet>
    </>
  );
}
