import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import { fetchHostedProjects, updateHostedProject } from './api';
import { ProjectDirectory } from './ProjectDirectory';
import {
  decodeHostedProjectsResponse,
  PROJECT_HOST,
  PROJECT_LINK_KIND,
  PROJECT_SOURCE,
  PROJECT_STATUS,
  type HostedProject,
} from './schema';

vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  fetchHostedProjects: vi.fn(),
  updateHostedProject: vi.fn(),
}));

const PROJECTS: HostedProject[] = [
  {
    id: 'new-project',
    services: [{ key: 'mac:com.jacobr.new-project', live: true }],
    name: 'Fresh scan',
    description: 'Waiting for review.',
    host: PROJECT_HOST.MAC,
    links: [
      {
        kind: PROJECT_LINK_KIND.LOCAL,
        label: 'This Mac',
        url: 'http://127.0.0.1:4400/',
      },
    ],
    status: PROJECT_STATUS.NEW,
    source: PROJECT_SOURCE.SCANNED,
    live: true,
    first_seen: '2026-10-16T18:00:00Z',
    last_seen: '2026-10-16T18:15:00Z',
    updated_at: '2026-10-16T18:00:00Z',
  },
  {
    id: 'listed-project',
    services: [{ key: 'pi:listed.service', live: true }],
    name: 'Listed app',
    description: 'Available from every dashboard device.',
    host: PROJECT_HOST.PI,
    links: [
      {
        kind: PROJECT_LINK_KIND.LOCAL,
        label: 'This Mac',
        url: 'http://127.0.0.1:8792/',
      },
      { kind: PROJECT_LINK_KIND.LAN, label: 'LAN', url: 'http://vanpi.lan:8791/' },
      {
        kind: PROJECT_LINK_KIND.TAILSCALE,
        label: 'Tailscale',
        url: 'http://100.82.91.76:8791/',
      },
    ],
    status: PROJECT_STATUS.LISTED,
    source: PROJECT_SOURCE.SCANNED,
    live: true,
    first_seen: '2026-10-16T18:00:00Z',
    last_seen: '2026-10-16T18:15:00Z',
    updated_at: '2026-10-16T18:00:00Z',
  },
  {
    id: 'hidden-project',
    services: [{ key: 'web:hidden-project', live: true }],
    name: 'Hidden app',
    description: 'Intentionally hidden.',
    host: PROJECT_HOST.WEB,
    links: [
      {
        kind: PROJECT_LINK_KIND.WEB,
        label: 'Web',
        url: 'https://hidden-project.lionfeather.dev/',
      },
    ],
    status: PROJECT_STATUS.HIDDEN,
    source: PROJECT_SOURCE.SCANNED,
    live: true,
    first_seen: '2026-10-16T18:00:00Z',
    last_seen: '2026-10-16T18:15:00Z',
    updated_at: '2026-10-16T18:00:00Z',
  },
  {
    id: 'stale-project',
    services: [{ key: 'web:stale', live: false }],
    name: 'Offline kept app',
    description: 'Kept, but no longer serves a page.',
    host: PROJECT_HOST.CUSTOM,
    links: [{ kind: PROJECT_LINK_KIND.WEB, label: 'Web', url: 'https://example.com/' }],
    status: PROJECT_STATUS.LISTED,
    source: PROJECT_SOURCE.SCANNED,
    live: false,
    first_seen: null,
    last_seen: null,
    updated_at: '2026-10-16T18:00:00Z',
  },
  ...[PROJECT_STATUS.NEW, PROJECT_STATUS.HIDDEN].map((status) => ({
    id: `stale-${status}`,
    services: [{ key: `web:stale-${status}`, live: false }],
    name: `Offline ${status} app`,
    description: '',
    host: PROJECT_HOST.CUSTOM,
    links: [{ kind: PROJECT_LINK_KIND.WEB, label: 'Web', url: `https://${status}.example/` }],
    status,
    source: PROJECT_SOURCE.SCANNED,
    live: false,
    first_seen: null,
    last_seen: null,
    updated_at: '2026-10-16T18:00:00Z',
  })),
];

beforeAll(() => {
  if (!HTMLDialogElement.prototype.showModal) {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.setAttribute('open', '');
    };
  }
  if (!HTMLDialogElement.prototype.close) {
    HTMLDialogElement.prototype.close = function close() {
      this.removeAttribute('open');
      this.dispatchEvent(new Event('close'));
    };
  }
});

beforeEach(() => {
  vi.mocked(fetchHostedProjects).mockResolvedValue(PROJECTS);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderDirectory() {
  render(
    <ToastProvider>
      <ProjectDirectory open onClose={vi.fn()} />
    </ToastProvider>,
  );
}

function mockUpdates() {
  let current = PROJECTS;
  vi.mocked(updateHostedProject).mockImplementation(async (id, patch) => {
    current = current.map((project) => (project.id === id ? { ...project, ...patch } : project));
    return current;
  });
}

describe('ProjectDirectory', () => {
  it('renders projects, dims offline kept ones, and leaves out other offline ones', async () => {
    renderDirectory();

    expect(await screen.findByRole('heading', { name: 'New' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Listed' })).toBeInTheDocument();
    expect(screen.getByText('Hidden (1)').closest('details')).not.toHaveAttribute('open');
    const listedRow = screen.getByRole('link', { name: 'Listed app' }).closest('li');
    expect(listedRow).not.toBeNull();
    expect(within(listedRow!).getByText('This Mac')).toBeInTheDocument();
    expect(within(listedRow!).getByText('LAN')).toBeInTheDocument();
    expect(within(listedRow!).getByText('TS')).toBeInTheDocument();
    expect(screen.getByText('Vanpi')).toBeInTheDocument();
    expect(screen.getByText('lionfeather.dev')).toBeInTheDocument();
    const offlineRow = screen.getByRole('link', { name: 'Offline kept app' }).closest('li');
    expect(offlineRow).toHaveClass('project-directory__row--stale');
    expect(within(offlineRow!).getByText('offline')).toBeInTheDocument();
    expect(screen.queryByText('Offline new app')).not.toBeInTheDocument();
    expect(screen.queryByText('Offline hidden app')).not.toBeInTheDocument();
  });

  it('keeps, hides, and unhides projects with full-list replacements', async () => {
    mockUpdates();
    renderDirectory();
    await screen.findByRole('link', { name: 'Fresh scan' });

    fireEvent.click(screen.getByRole('button', { name: 'Keep Fresh scan' }));
    await waitFor(() =>
      expect(updateHostedProject).toHaveBeenCalledWith('new-project', {
        status: PROJECT_STATUS.LISTED,
      }),
    );
    expect(screen.queryByRole('heading', { name: 'New' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Hide Listed app' }));
    await waitFor(() => expect(screen.getByText('Hidden (2)')).toBeInTheDocument());
    expect(updateHostedProject).toHaveBeenCalledWith('listed-project', {
      status: PROJECT_STATUS.HIDDEN,
    });

    fireEvent.click(screen.getByText('Hidden (2)'));
    fireEvent.click(screen.getByRole('button', { name: 'Unhide Hidden app' }));
    await waitFor(() =>
      expect(updateHostedProject).toHaveBeenCalledWith('hidden-project', {
        status: PROJECT_STATUS.LISTED,
      }),
    );
    expect(screen.getByRole('link', { name: 'Hidden app' })).toBeInTheDocument();
  });

  it('saves inline name and description edits', async () => {
    mockUpdates();
    renderDirectory();
    await screen.findByRole('link', { name: 'Listed app' });

    fireEvent.click(screen.getByRole('button', { name: 'Edit Listed app' }));
    const form = screen.getByRole('button', { name: 'Save changes to Listed app' }).closest('form');
    expect(form).not.toBeNull();
    fireEvent.change(within(form!).getByLabelText('Name'), { target: { value: 'Renamed app' } });
    fireEvent.change(within(form!).getByLabelText('Description'), {
      target: { value: 'Updated description.' },
    });
    fireEvent.click(within(form!).getByRole('button', { name: 'Save changes to Listed app' }));

    await waitFor(() =>
      expect(updateHostedProject).toHaveBeenCalledWith('listed-project', {
        name: 'Renamed app',
        description: 'Updated description.',
      }),
    );
    expect(screen.getByRole('link', { name: 'Renamed app' })).toHaveAttribute(
      'title',
      'Updated description.',
    );
  });

  it('surfaces update errors and keeps the inline editor open', async () => {
    vi.mocked(updateHostedProject).mockRejectedValue(new Error('A project with that name exists'));
    renderDirectory();
    await screen.findByRole('link', { name: 'Listed app' });

    fireEvent.click(screen.getByRole('button', { name: 'Edit Listed app' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes to Listed app' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('A project with that name exists');
    expect(screen.getByRole('button', { name: 'Save changes to Listed app' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('A project with that name exists');
  });

  it('surfaces a response decode failure in the error paragraph', async () => {
    vi.mocked(fetchHostedProjects).mockImplementation(async () =>
      decodeHostedProjectsResponse({
        ok: true,
        projects: [
          {
            ...PROJECTS[0],
            links: [{ kind: PROJECT_LINK_KIND.WEB, label: 'Web', url: 'javascript:alert(1)' }],
          },
        ],
      }),
    );
    renderDirectory();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'project directory response.projects[0].links[0].url must be a safe http or https URL',
    );
  });
});
