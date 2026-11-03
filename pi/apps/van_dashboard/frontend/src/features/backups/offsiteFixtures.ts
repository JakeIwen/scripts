const NOW = 1_700_000_000;

const entry = (finished: number, extra: Record<string, unknown> = {}) => ({
  result: 'ok',
  snapshot: 'abcdef0123456789',
  logical_bytes_processed: 5_368_709_120,
  newly_added_packed_bytes: 1_073_741_824,
  duration_seconds: 3600,
  finished,
  target_kind: 'local',
  ...extra,
});

/** A v2 `/api/backups/offsite` payload: a local seed running exfat512, earlier sources in history. */
export function offsiteSeedingPayload(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    supported: { history: true },
    status: {
      result: 'running',
      label: 'FORT-MPLS-5TB',
      target_kind: 'local',
      source: 'exfat512',
      bytes_done: 21_474_836_480,
      bytes_total: 107_374_182_400,
      percent: 21.4,
      rate: 52_428_800,
      eta: 5400,
      started: NOW - 7200,
      updated: NOW - 5,
      cleanup_errors: [],
      sources: { exfat512: { result: 'running' } },
    },
    history: {
      'FORT-MPLS-5TB': {
        movingparts: { latest: entry(NOW - 86_400), last_ok: entry(NOW - 86_400) },
        borg: {
          latest: entry(NOW - 3600, { result: 'failed', cause: 'repository locked' }),
          last_ok: entry(NOW - 172_800, { snapshot: '99887766aabbccdd' }),
        },
      },
    },
    evidence: { check: null, restore_test: null },
    target_drive: {
      label: 'FORT-MPLS-5TB',
      mounted: true,
      used_bytes: 2_199_023_255_552,
      total_bytes: 5_497_558_138_880,
    },
    ...overrides,
  };
}

/** What the deployed v1 script yields: current run only, no history or evidence. */
export function offsiteV1Payload() {
  return {
    ok: true,
    supported: { history: false },
    status: {
      result: 'ok',
      label: 'FORT-MPLS-5TB',
      target_kind: 'local',
      finished: NOW - 3600,
      duration_seconds: 7200,
      cleanup_errors: [],
      sources: { exfat512: { result: 'ok', snapshot: '1234567890abcdef' } },
    },
    history: {},
    evidence: { check: null, restore_test: null },
    target_drive: null,
  };
}

export function offsiteRemotePayload() {
  return {
    ok: true,
    supported: { history: true },
    status: {
      result: 'ok',
      label: 'NAS-14TB',
      target_kind: 'remote',
      finished: NOW - 7200,
      duration_seconds: 600,
      cleanup_errors: [],
      sources: {},
    },
    history: {
      'NAS-14TB': {
        borg: {
          latest: entry(NOW - 7200, { target_kind: 'remote', receipt: 'feedface00112233' }),
          last_ok: entry(NOW - 7200, { target_kind: 'remote', receipt: 'feedface00112233' }),
        },
      },
    },
    evidence: {
      check: { result: 'ok', finished: NOW - 86_400, duration_seconds: 30 },
      restore_test: { result: 'failed', finished: NOW - 172_800, cause: 'file mismatch' },
    },
    target_drive: null,
  };
}
