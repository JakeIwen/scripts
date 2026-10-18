import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import type { PollingState } from '../../hooks/usePollingResource';
import type { CrashAnalysisControls } from './crashControls';
import { SystemHealthSheet } from './SystemHealthSheet';
import { sampleSystemHealth } from './testFixtures';
import type { CrashHistory, SystemHealthReport } from './types';

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

function resource(report: SystemHealthReport): PollingState<SystemHealthReport> {
  return {
    data: report,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn(async () => report),
  };
}

function crashHistory(): PollingState<CrashHistory> {
  const data = { items: [] };
  return {
    data,
    error: null,
    initialLoading: false,
    refreshing: false,
    lastUpdatedAt: Date.now(),
    refresh: vi.fn(async () => data),
  };
}

function crashControls(): CrashAnalysisControls {
  return {
    running: false,
    result: null,
    error: null,
    analyze: vi.fn().mockResolvedValue(undefined),
  };
}

describe('system health UI', () => {
  it('renders projected details and reports range changes', () => {
    const onRangeChange = vi.fn();
    render(
      <SystemHealthSheet
        open
        onClose={vi.fn()}
        rangeHours={6}
        onRangeChange={onRangeChange}
        resource={resource(sampleSystemHealth())}
        crashHistory={crashHistory()}
        crashAnalysis={crashControls()}
      />,
    );

    expect(screen.getByText('smbd')).toBeInTheDocument();
    expect(screen.getByText('USB device reset')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Analyze previous crash' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '24 hours' }));
    expect(onRangeChange).toHaveBeenCalledWith(24);
  });
});
