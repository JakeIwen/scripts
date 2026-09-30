import {
  arrayValue,
  nullableNumber,
  nullableString,
  numberValue,
  objectValue,
  optionalString,
  stringValue,
} from '../../api/validation';
import type {
  NetworkCoverage,
  NetworkEvent,
  NetworkHistoryReport,
  NetworkIncident,
  NetworkIncidentDetail,
} from './types';

function strings(value: unknown, label: string): string[] {
  return arrayValue(value, label).map((item) => stringValue(item, label));
}

function envelope(value: unknown): Record<string, unknown> {
  const object = objectValue(value, 'network history');
  if (object.ok !== true || object.schema_version !== 1) {
    throw new TypeError('Unsupported network history response');
  }
  return object;
}

function event(value: unknown): NetworkEvent {
  const row = objectValue(value, 'event');
  return {
    id: numberValue(row.id, 'event.id'),
    record_id: optionalString(row.record_id, 'event.record_id'),
    time: nullableNumber(row.time, 'event.time'),
    source_time: nullableNumber(row.source_time, 'event.source_time'),
    received_at: nullableNumber(row.received_at, 'event.received_at'),
    imported_at: numberValue(row.imported_at, 'event.imported_at'),
    source: stringValue(row.source, 'event.source'),
    device: stringValue(row.device, 'event.device'),
    uplink: nullableString(row.uplink, 'event.uplink'),
    kind: stringValue(row.kind, 'event.kind'),
    severity: stringValue(row.severity, 'event.severity'),
    message: stringValue(row.message, 'event.message'),
    time_quality: stringValue(row.time_quality, 'event.time_quality'),
    provenance: objectValue(row.provenance, 'event.provenance'),
    state: optionalString(row.state, 'event.state'),
    domain: optionalString(row.domain, 'event.domain'),
    stream: optionalString(row.stream, 'event.stream'),
    boot_id: row.boot_id === undefined ? undefined : nullableString(row.boot_id, 'event.boot_id'),
    monotonic:
      row.monotonic === undefined ? undefined : nullableNumber(row.monotonic, 'event.monotonic'),
  };
}

function incident(value: unknown): NetworkIncident {
  const row = objectValue(value, 'incident');
  return {
    id: stringValue(row.id, 'incident.id'),
    onset: numberValue(row.onset, 'incident.onset'),
    end: nullableNumber(row.end, 'incident.end'),
    status: stringValue(row.status, 'incident.status'),
    domains: strings(row.domains, 'incident.domains'),
    uplinks: strings(row.uplinks, 'incident.uplinks'),
    summary: stringValue(row.summary, 'incident.summary'),
    observations: strings(row.observations, 'incident.observations'),
    hypotheses: strings(row.hypotheses, 'incident.hypotheses'),
    evidence_ids: arrayValue(row.evidence_ids, 'incident.evidence_ids').map((id) =>
      numberValue(id, 'evidence ID'),
    ),
    duration: row.duration === undefined ? undefined : nullableNumber(row.duration, 'duration'),
    latest_evidence_at:
      row.latest_evidence_at === undefined
        ? undefined
        : nullableNumber(row.latest_evidence_at, 'latest evidence time'),
  };
}

function coverage(value: unknown): NetworkCoverage {
  const row = objectValue(value, 'coverage');
  return {
    source: stringValue(row.source, 'coverage.source'),
    device: stringValue(row.device, 'coverage.device'),
    status: stringValue(row.status, 'coverage.status'),
    last_event_at: nullableNumber(row.last_event_at, 'coverage.last_event_at'),
    last_received_at: nullableNumber(row.last_received_at, 'coverage.last_received_at'),
    detail: stringValue(row.detail, 'coverage.detail'),
  };
}

export function decodeNetworkHistory(value: unknown): NetworkHistoryReport {
  const data = envelope(value);
  const range = objectValue(data.range, 'range');
  const counts = objectValue(data.counts, 'counts');
  const filters = objectValue(data.filters, 'filters');
  return {
    schema_version: 1,
    generated_at: numberValue(data.generated_at, 'generated_at'),
    range: {
      start: numberValue(range.start, 'range.start'),
      end: numberValue(range.end, 'range.end'),
    },
    coverage: arrayValue(data.coverage, 'coverage').map(coverage),
    events: arrayValue(data.events, 'events').map(event),
    incidents: arrayValue(data.incidents, 'incidents').map(incident),
    counts: {
      events: numberValue(counts.events, 'counts.events'),
      incidents: numberValue(counts.incidents, 'counts.incidents'),
    },
    limitations: strings(data.limitations, 'limitations'),
    time_semantics: objectValue(data.time_semantics, 'time_semantics'),
    filters: {
      uplinks: strings(filters.uplinks, 'filters.uplinks'),
      devices: strings(filters.devices, 'filters.devices'),
    },
  };
}

export function decodeNetworkIncident(value: unknown): NetworkIncidentDetail {
  const data = envelope(value);
  const counts = objectValue(data.counts, 'counts');
  return {
    schema_version: 1,
    generated_at: numberValue(data.generated_at, 'generated_at'),
    incident: incident(data.incident),
    events: arrayValue(data.events, 'events').map(event),
    coverage: arrayValue(data.coverage, 'coverage').map(coverage),
    limitations: strings(data.limitations, 'limitations'),
    time_semantics: objectValue(data.time_semantics, 'time_semantics'),
    counts: { events: numberValue(counts.events, 'counts.events') },
    raw: data,
  };
}
