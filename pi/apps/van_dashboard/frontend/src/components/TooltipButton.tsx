import { useId, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes } from 'react';
import { createPortal } from 'react-dom';
import './TooltipButton.css';

interface TooltipButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  tooltip: string;
}

export function TooltipButton({ tooltip, children, onClick, ...props }: TooltipButtonProps) {
  const id = useId();
  const anchor = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      if (!anchor.current || !bubble.current) return;
      const rect = anchor.current.getBoundingClientRect();
      const box = bubble.current.getBoundingClientRect();
      setPosition({
        left: Math.max(8, Math.min(rect.right - box.width, window.innerWidth - box.width - 8)),
        top:
          rect.bottom + box.height + 16 < window.innerHeight
            ? rect.bottom + 8
            : Math.max(8, rect.top - box.height - 8),
      });
    }
    function dismiss(event: PointerEvent) {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    }
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    document.addEventListener('pointerdown', dismiss);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
      document.removeEventListener('pointerdown', dismiss);
    };
  }, [open, tooltip]);
  return (
    <>
      <button
        {...props}
        ref={anchor}
        type="button"
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => {
          if (document.activeElement !== anchor.current) setOpen(false);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          setOpen(true);
          onClick?.(event);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape' && open) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          }
        }}
      >
        {children}
      </button>
      {open &&
        createPortal(
          <span ref={bubble} id={id} role="tooltip" className="status-tooltip" style={position}>
            {tooltip}
          </span>,
          anchor.current?.closest('dialog') ?? document.body,
        )}
    </>
  );
}
