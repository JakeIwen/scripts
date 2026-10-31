import { TooltipButton } from '../TooltipButton';
import './mediaControls.css';

export function MediaHelp({ label, children }: { label: string; children: string }) {
  return (
    <TooltipButton className="media-help" aria-label={`About ${label}`} tooltip={children}>
      ?
    </TooltipButton>
  );
}
