export type StatusTone = 'neutral' | 'good' | 'warning' | 'bad';

interface StatusPillProps {
  children: React.ReactNode;
  tone?: StatusTone;
  dot?: boolean;
  detail?: string;
  label?: string;
}

export function StatusPill({
  children,
  tone = 'neutral',
  dot = false,
  detail,
  label,
}: StatusPillProps) {
  const content = (
    <>
      {dot && <span className="status-pill__dot" aria-hidden="true" />}
      <span>{children}</span>
    </>
  );
  const className = `status-pill status-pill--${tone}`;
  return detail ? (
    <TooltipButton className={className} tooltip={detail} aria-label={label}>
      {content}
    </TooltipButton>
  ) : (
    <span className={className}>{content}</span>
  );
}
import { TooltipButton } from './TooltipButton';
