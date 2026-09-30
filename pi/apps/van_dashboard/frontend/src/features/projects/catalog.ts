/** Human-maintained directory of web projects. Keep addresses here, not in JSX. */
export interface HostedProject {
  id: string;
  name: string;
  icon: string;
  host: 'This Mac' | 'Vanpi';
  description: string;
  note?: string;
  links: { label: string; url: string }[];
}

const VANPI_LAN = 'vanpi.lan';
const VANPI_TAILSCALE = '100.82.91.76';

function piLinks(port: number, path = '/') {
  return [
    { label: 'LAN', url: `http://${VANPI_LAN}:${port}${path}` },
    { label: 'Tailscale', url: `http://${VANPI_TAILSCALE}:${port}${path}` },
  ];
}

export const HOSTED_PROJECTS: HostedProject[] = [
  {
    id: 'promaster-library',
    name: 'ProMaster Service Library',
    icon: '📚',
    host: 'This Mac',
    description: 'Search the 2022 ProMaster service documentation and diagrams.',
    links: [{ label: 'This Mac', url: 'http://127.0.0.1:8766/' }],
  },
  {
    id: 'fieldwork',
    name: 'Fieldwork',
    icon: '💼',
    host: 'This Mac',
    description: 'Job-search workspace.',
    links: [{ label: 'This Mac', url: 'http://127.0.0.1:4317/' }],
  },
  {
    id: 'telemetry',
    name: 'Vehicle Telemetry',
    icon: '📊',
    host: 'Vanpi',
    description: 'Vehicle readings, drive data, and diagnostics.',
    links: piLinks(8765),
  },
  {
    id: 'network-history',
    name: 'Network History',
    icon: '🌐',
    host: 'Vanpi',
    description: 'Network flight recorder: connectivity history, incidents, and evidence.',
    links: piLinks(8788, '/#network-history?hours=6'),
  },
  {
    id: 'audiobooks',
    name: 'Audiobooks',
    icon: '📖',
    host: 'Vanpi',
    description: 'Browse the audiobook library and play through Sonos.',
    links: piLinks(8787),
  },
  {
    id: 'movies-tv',
    name: 'Movies & TV',
    icon: '🎬',
    host: 'Vanpi',
    description: 'Browse, play, and resume movies and shows.',
    links: piLinks(8789),
  },
  {
    id: 'home-assistant',
    name: 'Home Assistant',
    icon: '🏠',
    host: 'Vanpi',
    description: 'Smart-home devices, automations, and settings.',
    links: piLinks(8123),
  },
  {
    id: 'soundbytes',
    name: 'Sound Library',
    icon: '🔊',
    host: 'Vanpi',
    description: 'Browse the sound files hosted on the Pi.',
    links: piLinks(8000),
  },
];
