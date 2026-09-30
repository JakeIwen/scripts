export interface NetworkEvent {
  id: number;
  record_id?: string;
  time: number | null;
  source_time: number | null;
  received_at: number | null;
  imported_at: number;
  source: string;
  device: string;
  uplink: string | null;
  kind: string;
  severity: string;
  message: string;
  time_quality: string;
  provenance: Record<string, unknown>;
  state?: string;
  domain?: string;
  stream?: string;
  boot_id?: string | null;
  monotonic?: number | null;
}

export interface NetworkIncident {
  id: string;
  onset: number;
  end: number | null;
  status: string;
  domains: string[];
  uplinks: string[];
  summary: string;
  observations: string[];
  hypotheses: string[];
  evidence_ids: number[];
  duration?: number | null;
  latest_evidence_at?: number | null;
}

export interface NetworkCoverage {
  source: string;
  device: string;
  status: string;
  last_event_at: number | null;
  last_received_at: number | null;
  detail: string;
}

export interface NetworkHistoryReport {
  schema_version: number;
  generated_at: number;
  range: { start: number; end: number };
  coverage: NetworkCoverage[];
  events: NetworkEvent[];
  incidents: NetworkIncident[];
  counts: { events: number; incidents: number };
  limitations: string[];
  time_semantics: Record<string, unknown>;
  filters: { uplinks: string[]; devices: string[] };
}

export interface NetworkIncidentDetail {
  schema_version: number;
  generated_at: number;
  incident: NetworkIncident;
  events: NetworkEvent[];
  limitations: string[];
  coverage: NetworkCoverage[];
  time_semantics: Record<string, unknown>;
  counts: { events: number };
  raw: Record<string, unknown>;
}

export interface HistoryQuery {
  hours?: number;
  start?: number;
  end?: number;
  uplink?: string;
  device?: string;
  search?: string;
}
