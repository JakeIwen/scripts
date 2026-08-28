import { COMPUTE_RANGES, type ComputeRange } from './types';
import { computeRangeLabel } from './presentation';

interface ComputeRangeSelectorProps {
  value: ComputeRange;
  disabled: boolean;
  onChange: (range: ComputeRange) => void;
}

export function ComputeRangeSelector({ value, disabled, onChange }: ComputeRangeSelectorProps) {
  return (
    <div className="compute-ranges" role="group" aria-label="Compute metrics time range">
      {COMPUTE_RANGES.map((range) => (
        <button
          type="button"
          key={range}
          aria-pressed={range === value}
          disabled={disabled}
          onClick={() => onChange(range)}
        >
          {computeRangeLabel(range)}
        </button>
      ))}
    </div>
  );
}
