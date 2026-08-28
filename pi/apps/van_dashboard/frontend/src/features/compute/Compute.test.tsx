import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { PollingState } from '../../hooks/usePollingResource';
import { ComputeSheet } from './ComputeSheet';
import { ComputeTile } from './ComputeTile';
import { sampleComputeReport } from './testFixtures';
import type { ComputeReport } from './types';

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

function resource(report: ComputeReport): PollingState<ComputeReport> {
  return {
    data: report,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn(async () => report),
  };
}

describe('compute UI', () => {
  it('summarizes scheduler and measured work on the tile', () => {
    const onOpen = vi.fn();
    render(
      <ComputeTile
        report={sampleComputeReport()}
        error={null}
        refreshing={false}
        onOpen={onOpen}
      />,
    );

    expect(screen.getByText('m4mac · ready')).toBeInTheDocument();
    expect(screen.getByText('6.50 s')).toBeInTheDocument();
    expect(screen.getByText('Metrics and queue history · read-only')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open M4 compute details' }));
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('renders task and job projections and reports range changes', () => {
    const onRangeChange = vi.fn();
    render(
      <ComputeSheet
        open
        onClose={vi.fn()}
        rangeHours={168}
        onRangeChange={onRangeChange}
        resource={resource(sampleComputeReport())}
      />,
    );

    expect(screen.getAllByText('repo-tests').length).toBeGreaterThan(0);
    expect(screen.getByText('Task exited with code 1')).toBeInTheDocument();
    expect(screen.getByText('Worker Unavailable')).toBeInTheDocument();
    expect(
      screen.getByText(/diagnostics and retained output are loaded only when expanded/i),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '24 hours' }));
    expect(onRangeChange).toHaveBeenCalledWith(24);
  });
});
