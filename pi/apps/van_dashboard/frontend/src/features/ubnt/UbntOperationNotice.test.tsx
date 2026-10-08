import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { UbntOperationNotice } from './UbntOperationNotice';
import { sampleUbntStatus } from './testFixtures';

afterEach(cleanup);

describe('antenna confirmation notice', () => {
  it('explains an uncertain switch without calling Starlink a failure, then clears on recovery', () => {
    const operation = {
      ...sampleUbntStatus('error').operation,
      kind: 'starlink' as const,
      confirmationPending: true,
      error: 'UBNT starlink-off could not be confirmed yet',
    };
    const view = render(<UbntOperationNotice operation={operation} />);
    expect(screen.getByText('Wi-Fi change awaiting confirmation')).toBeInTheDocument();
    expect(screen.getByText(/may still be switching networks/)).toBeInTheDocument();
    expect(screen.queryByText(/failed/)).not.toBeInTheDocument();
    expect(view.container.querySelector('.ubnt-operation--error')).toBeNull();
    expect(screen.queryByText(/UBNT starlink-off/)).not.toBeInTheDocument();

    view.rerender(
      <UbntOperationNotice
        operation={{
          ...operation,
          status: 'complete',
          confirmationPending: false,
          error: null,
          message: "Antenna connected to Admirals' Club!",
        }}
      />,
    );
    expect(screen.getByText("Antenna connected to Admirals' Club!")).toBeInTheDocument();
    expect(screen.queryByText(/awaiting confirmation/)).not.toBeInTheDocument();
    expect(screen.queryByText(/may still be switching networks/)).not.toBeInTheDocument();
    expect(view.container.querySelector('.ubnt-operation--error')).toBeNull();
  });

  it('keeps an actual antenna error distinct from Starlink power', () => {
    const operation = {
      ...sampleUbntStatus('error').operation,
      kind: 'starlink' as const,
      error: 'The saved denlink profile is missing',
    };
    const { container } = render(<UbntOperationNotice operation={operation} />);
    expect(screen.getByText('Antenna switch failed')).toBeInTheDocument();
    expect(screen.getByText('The saved denlink profile is missing')).toBeInTheDocument();
    expect(container.querySelector('.ubnt-operation--error')).not.toBeNull();
  });

  it('does not display an empty operation after startup', () => {
    const { container } = render(
      <UbntOperationNotice operation={sampleUbntStatus('idle').operation} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
