/** Human-maintained directory of web projects. Keep addresses here, not in JSX. */
export interface ProjectLink {
  kind: 'local' | 'lan' | 'ts' | 'web';
  label: string;
  url: string;
}

const LINK_PRIORITY = { local: 0, lan: 1, ts: 2, web: 3 };

export function preferredProjectLink(links: ProjectLink[]): ProjectLink | undefined {
  return [...links].sort((a, b) => LINK_PRIORITY[a.kind] - LINK_PRIORITY[b.kind])[0];
}

export interface HostedProject {
  id: string;
  name: string;
  host: 'This Mac' | 'Vanpi';
  description: string;
  note?: string;
  links: ProjectLink[];
}

const VANPI_LAN = 'vanpi.lan';
const VANPI_TAILSCALE = '100.82.91.76';

function piLinks(port: number, path = '/'): ProjectLink[] {
  return [
    { kind: 'lan', label: 'LAN', url: `http://${VANPI_LAN}:${port}${path}` },
    { kind: 'ts', label: 'Tailscale', url: `http://${VANPI_TAILSCALE}:${port}${path}` },
  ];
}

export const HOSTED_PROJECTS: HostedProject[] = [
  {
    id: 'promaster-library',
    name: 'ProMaster Service Library',
    host: 'This Mac',
    description: 'Search the 2022 ProMaster service documentation and diagrams.',
    links: [{ kind: 'local', label: 'This Mac', url: 'http://127.0.0.1:8766/' }],
  },
  {
    id: 'fieldwork',
    name: 'Fieldwork',
    host: 'This Mac',
    description: 'Job-search workspace.',
    links: [{ kind: 'local', label: 'This Mac', url: 'http://127.0.0.1:4317/' }],
  },
  {
    id: 'telemetry',
    name: 'Vehicle Telemetry',
    host: 'Vanpi',
    description: 'Vehicle readings, drive data, and diagnostics.',
    links: piLinks(8765),
  },
  {
    id: 'network-history',
    name: 'Network History',
    host: 'Vanpi',
    description: 'Network flight recorder: connectivity history, incidents, and evidence.',
    links: piLinks(8788, '/#network-history?hours=6'),
  },
  {
    id: 'audiobooks',
    name: 'Audiobooks',
    host: 'Vanpi',
    description: 'Browse the audiobook library and play through Sonos.',
    links: piLinks(8787),
  },
  {
    id: 'movies-tv',
    name: 'Movies & TV',
    host: 'Vanpi',
    description: 'Browse, play, and resume movies and shows.',
    links: piLinks(8789),
  },
  {
    id: 'home-assistant',
    name: 'Home Assistant',
    host: 'Vanpi',
    description: 'Smart-home devices, automations, and settings.',
    links: piLinks(8123),
  },
  {
    id: 'soundbytes',
    name: 'Sound Library',
    host: 'Vanpi',
    description: 'Browse the sound files hosted on the Pi.',
    links: piLinks(8000),
  },
];
