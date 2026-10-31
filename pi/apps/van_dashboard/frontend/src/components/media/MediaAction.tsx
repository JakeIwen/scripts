import type { ButtonHTMLAttributes } from 'react';
import { MediaHelp } from './MediaHelp';

/** The separate hint remains reachable on touch and when the action is disabled. */
export function MediaAction({
  label,
  help,
  children,
  className = 'secondary-button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; help: string }) {
  return (
    <span className="media-action">
      <button {...props} type="button" className={className}>
        {children ?? label}
      </button>
      <MediaHelp label={label}>{help}</MediaHelp>
    </span>
  );
}
