import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../components/ToastProvider';
import { addHostedProject, fetchHostedProjects } from './api';
import { NewProjectForm } from './NewProjectForm';

vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  addHostedProject: vi.fn(),
  fetchHostedProjects: vi.fn(),
}));
afterEach(cleanup);

function showForm() {
  const onSaved = vi.fn();
  render(
    <ToastProvider>
      <NewProjectForm projects={[]} disabled={false} onSaved={onSaved} />
    </ToastProvider>,
  );
  fireEvent.click(screen.getByText('New Hosted Project'));
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Test project' } });
  return onSaved;
}

describe('New Hosted Project', () => {
  it('requires a URL then saves once with all four URL types', async () => {
    vi.mocked(addHostedProject).mockResolvedValue([]);
    const onSaved = showForm();
    fireEvent.click(screen.getByRole('button', { name: 'Add project' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Enter at least one URL');
    expect(addHostedProject).not.toHaveBeenCalled();
    for (const label of ['Local', 'LAN', 'TS', 'Web']) {
      fireEvent.change(screen.getByLabelText(`${label} URL`), {
        target: { value: `https://example.com/${label}` },
      });
    }
    const button = screen.getByRole('button', { name: 'Add project' });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith([]));
    expect(addHostedProject).toHaveBeenCalledOnce();
    expect(addHostedProject).toHaveBeenCalledWith({
      name: 'Test project',
      local: 'https://example.com/Local',
      lan: 'https://example.com/LAN',
      ts: 'https://example.com/TS',
      web: 'https://example.com/Web',
    });
  });

  it('keeps the draft and reconciles after an ambiguous save failure', async () => {
    vi.mocked(addHostedProject).mockRejectedValue(new Error('Response lost'));
    vi.mocked(fetchHostedProjects).mockResolvedValue([]);
    const onSaved = showForm();
    fireEvent.change(screen.getByLabelText('Web URL'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add project' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith([]));
    expect(screen.getByLabelText('Name')).toHaveValue('Test project');
    expect(screen.getByRole('alert')).toHaveTextContent('Response lost');
    expect(addHostedProject).toHaveBeenCalledOnce();
    expect(fetchHostedProjects).toHaveBeenCalledOnce();
  });
});
