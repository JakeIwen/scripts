import { TooltipButton } from '../../components/TooltipButton';

/** One label/value pair of an offsite card, with an optional hover/focus/tap hint. */
export function OffsiteRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt>
        {label}
        {hint && (
          <TooltipButton
            type="button"
            className="backup-offsite__info"
            aria-label={`About ${label}`}
            tooltip={hint}
          >
            i
          </TooltipButton>
        )}
      </dt>
      <dd>{children}</dd>
    </div>
  );
}
