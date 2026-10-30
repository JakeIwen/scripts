export type StatusTone = 'neutral' | 'good' | 'warning' | 'bad';

interface StatusPillProps {
  children: React.ReactNode;
  tone?: StatusTone;
  dot?: boolean;
}

export function StatusPill({ children, tone = 'neutral', dot = false }: StatusPillProps) {
  return (
    <span className={`status-pill status-pill--${tone}`}>
      {dot && <span className="status-pill__dot" aria-hidden="true" />}
      <span>{children}</span>
    </span>
  );
}
