import type { Consumer, HostState, MeteredStatus } from './schema';

function consumerFixtures(): Consumer[] {
  return [
    {
      id: 'pi.torrents',
      host: 'pi',
      label: 'Torrents',
      how: 'tick',
      note: 'Disk and ignition safety still apply.',
    },
    {
      id: 'pi.icloud_backup',
      host: 'pi',
      label: 'Pi iCloud backup',
      how: 'guard',
      note: 'Fresh transport admission still applies.',
    },
    {
      id: 'pi.codex_update',
      host: 'pi',
      label: 'Codex updates',
      how: 'unprotected',
      note: 'Updater integration is pending.',
    },
    {
      id: 'mac.icloud',
      host: 'mac',
      label: 'Mac iCloud',
      how: 'advisory',
      note: 'Low Data Mode is advisory.',
    },
  ];
}

export function meteredFixture(): MeteredStatus {
  const now = Date.now() / 1000;
  const state: HostState = {
    host: 'pi',
    evaluated_at: now,
    verdict: {
      metered: true,
      reason: 'metered SSID selected',
      matched: [{ member: 'wan', ssid: 'denlink' }],
      evidence_at: now,
    },
    effective: { metered: true, source: 'verdict', episode: 'episode-1' },
    consumers: {
      'pi.torrents': {
        permitted: false,
        reason: 'held on a metered or unknown network',
        approval: null,
        actual: 'held',
      },
      'pi.icloud_backup': {
        permitted: false,
        reason: 'held on a metered or unknown network',
        approval: null,
        actual: 'unknown',
      },
      'pi.codex_update': {
        permitted: false,
        reason: 'held on a metered or unknown network',
        approval: null,
        actual: 'n/a',
      },
    },
    config_revision: 2,
    policy_revision: 3,
    route_signature: 'route',
    valid_until: now + 30,
  };
  return {
    ok: true,
    config: {
      schema: 1,
      revision: 2,
      metered_ssids: ['denlink'],
      home_network: { ssid: 'home', gateway: '192.0.2.1', gateway_mac: '00:11:22:33:44:55' },
      receipts: [],
    },
    policy: {
      schema: 1,
      revision: 3,
      mode: { value: 'auto', until: null, set_by: 'default', set_at: 0 },
      approvals: {},
      exempt: [],
    },
    state,
    registry: consumerFixtures(),
    mac_report: null,
  };
}
