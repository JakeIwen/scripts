import { describe, expect, it } from 'vitest';

import {
  decodeHostedProjectsResponse,
  PROJECT_HOST,
  PROJECT_LINK_KIND,
  PROJECT_SOURCE,
  PROJECT_STATUS,
} from './schema';

const PROJECT = {
  id: 'pi:audiobooks.service',
  key: 'pi:audiobooks.service',
  name: 'Audiobooks',
  description: 'Browse the audiobook library and play through Sonos.',
  host: PROJECT_HOST.PI,
  links: [
    {
      kind: PROJECT_LINK_KIND.LAN,
      label: 'LAN',
      url: 'http://vanpi.lan:8787/',
    },
    {
      kind: PROJECT_LINK_KIND.TAILSCALE,
      label: 'Tailscale',
      url: 'http://100.82.91.76:8787/',
    },
  ],
  status: PROJECT_STATUS.LISTED,
  source: PROJECT_SOURCE.SCANNED,
  live: true,
  first_seen: '2026-10-16T18:00:00Z',
  last_seen: '2026-10-16T18:15:00Z',
  updated_at: '2026-10-16T18:05:00Z',
};

describe('hosted project schema', () => {
  it('decodes a contract-shaped response', () => {
    expect(decodeHostedProjectsResponse({ ok: true, projects: [PROJECT] })).toEqual([PROJECT]);
  });

  it('rejects javascript URLs', () => {
    expect(() =>
      decodeHostedProjectsResponse({
        ok: true,
        projects: [
          {
            ...PROJECT,
            links: [{ kind: PROJECT_LINK_KIND.WEB, label: 'Web', url: 'javascript:alert(1)' }],
          },
        ],
      }),
    ).toThrow(
      'project directory response.projects[0].links[0].url must be a safe http or https URL',
    );
  });
});
