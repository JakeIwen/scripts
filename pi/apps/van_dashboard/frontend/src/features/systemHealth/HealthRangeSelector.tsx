import { SYSTEM_HEALTH_RANGES, type SystemHealthRange } from './types';
import { systemHealthRangeLabel } from './presentation';

interface HealthRangeSelectorProps {
  value: SystemHealthRange;
  disabled: boolean;
  onChange: (range: SystemHealthRange) => void;
}

export function HealthRangeSelector({ value, disabled, onChange }: HealthRangeSelectorProps) {
  return (
    <div className="system-health-ranges" role="group" aria-label="System health time range">
      {SYSTEM_HEALTH_RANGES.map((range) => (
        <button
          type="button"
          key={range}
          aria-pressed={range === value}
          disabled={disabled}
          onClick={() => onChange(range)}
        >
          {systemHealthRangeLabel(range)}
        </button>
      ))}
    </div>
  );
}
