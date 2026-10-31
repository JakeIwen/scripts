import type { OrganizerStatus, Plan, RunSummary } from './schema';
export function samplePlan(): Plan {
  return {
    id: 'plan-1',
    created_at: '2026-10-29T06:00:00Z',
    status: 'proposed',
    classifier: 'offline',
    classifier_error: null,
    existing_shows: { TV: ['Silo'], Documentaries: ['Planet Earth'] },
    run_ids: [],
    settled_rows: [],
    rows: [
      {
        entry: {
          name: 'Silo.S01E01',
          path: '/media/torrent/New/Silo.S01E01',
          kind: 'file',
          size_bytes: 100,
          torrent_hash: 'abc',
          torrent_name: 'Silo.S01E01',
          torrent_state: 'stalledUP',
          tracked: true,
          complete: true,
          skip_reason: null,
        },
        proposal: {
          category: 'TV',
          show: 'Silo',
          confidence: 0.95,
          reason: 'Matches existing show',
          existing_show: true,
          source: 'offline',
        },
        decision: { category: 'TV', show: 'Silo', accepted: true },
        target_path: '/media/torrent/TV/Silo/Silo.S01E01',
        auto_eligible: true,
      },
    ],
  };
}
export function sampleRun(): RunSummary {
  return {
    id: 'run-1',
    plan_id: 'plan-1',
    mode: 'manual',
    started_at: '2026-10-29T07:00:00Z',
    finished_at: '2026-10-29T07:01:00Z',
    moved_count: 2,
    failed_count: 1,
    reverted_count: 0,
    alias_status: 'scheduled',
    undoable: true,
    undone_at: null,
    error: null,
  };
}
export function sampleStatus(): OrganizerStatus {
  return {
    media_mounted: true,
    mirror_available: true,
    qbittorrent_available: true,
    new_count: 12,
    tracked_count: 10,
    untracked_count: 2,
    incomplete_count: 1,
    latest_plan: {
      id: 'plan-1',
      created_at: '2026-10-29T06:00:00Z',
      status: 'proposed',
      classifier: 'offline',
      row_count: 9,
      accepted_count: 6,
      auto_count: 6,
      classifier_error: null,
    },
    recent_runs: [sampleRun()],
    settings: {
      auto_apply: true,
      auto_apply_min_confidence: 0.85,
      auto_apply_new_shows: false,
      classifier: 'codex',
      codex_model: '',
      mirror_rename: true,
      run_alias_after_apply: true,
    },
    busy: null,
    last_error: null,
    job: null,
  };
}
