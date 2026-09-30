import type { NetworkHistoryReport, NetworkIncidentDetail } from './types';

// Deliberately synthetic: independent observations distinguish test-destination
// failure from a gateway failure without attributing an entire provider outage.
export function historyFixture(): NetworkHistoryReport {
  return {
    schema_version: 1,
    generated_at: 1700000300,
    range: { start: 1700000000, end: 1700000300 },
    coverage: [
      {
        source: 'openwrt-syslog',
        device: 'router',
        status: 'stale',
        last_event_at: 1700000040,
        last_received_at: 1700000040,
        detail: 'No recent probe heartbeat; gap is not an outage measurement.',
      },
    ],
    events: [
      {
        id: 11,
        record_id: 'fixture-physical-record-11',
        time: 1700000010,
        source_time: null,
        received_at: 1700000010,
        imported_at: 1700000200,
        source: 'openwrt-syslog',
        device: 'router',
        uplink: 'clientwan',
        kind: 'probe',
        severity: 'warning',
        message: 'gateway=reachable public_tests=failed password=[REDACTED]',
        time_quality: 'receiver-time',
        provenance: { file: 'openwrt.log.1', offset: 128, backfill: true },
        state: 'failure',
        domain: 'upstream-tests',
        stream: 'clientwan-probe',
      },
    ],
    incidents: [
      {
        id: 'incident-11',
        onset: 1700000010,
        end: 1700000040,
        status: 'recovered',
        domains: ['upstream-tests'],
        uplinks: ['clientwan'],
        summary: 'External test destinations failed while gateway responded',
        observations: ['Gateway responded during failed public destination tests.'],
        hypotheses: [
          'Upstream path may have been interrupted; destinations may have failed independently.',
        ],
        evidence_ids: [11],
        latest_evidence_at: 1700000040,
      },
    ],
    counts: { events: 1200, incidents: 1 },
    limitations: ['UDP syslog is best-effort; exact loss is unknown.'],
    time_semantics: {
      ordering: 'Receiver timestamps do not prove cross-device order.',
      import_time: 'Time inserted; never substitutes for historical incident time.',
    },
    filters: { uplinks: ['wan', 'clientwan'], devices: ['router', 'vanpi'] },
  };
}

export function detailFixture(): NetworkIncidentDetail {
  const report = historyFixture();
  const data = { ...report, ok: true, incident: report.incidents[0]!, counts: { events: 1 } };
  return { ...data, raw: data };
}
