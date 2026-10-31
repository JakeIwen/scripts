import type { CSSProperties, InputHTMLAttributes } from 'react';
import './LightingRange.css';

interface LightingRangeProps extends InputHTMLAttributes<HTMLInputElement> {
  min: number;
  max: number;
  value: number;
  thumbValue?: string;
}

export function LightingRange({ thumbValue, ...input }: LightingRangeProps) {
  if (thumbValue === undefined) return <input {...input} type="range" />;
  const progress = Math.max(0, Math.min(1, (input.value - input.min) / (input.max - input.min)));
  const style = { '--lighting-range-progress': progress } as CSSProperties;
  return (
    <span className="lighting-range" style={style}>
      <input {...input} type="range" />
      <output className="lighting-range__value" aria-hidden="true">
        {thumbValue}
      </output>
    </span>
  );
}
