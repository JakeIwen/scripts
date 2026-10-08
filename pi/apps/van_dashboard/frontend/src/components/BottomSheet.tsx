import { useEffect, useId, useRef, useState } from 'react';
import { formatRelativeTime } from '../utils/format';

interface BottomSheetProps {
  open: boolean;
  title: string;
  description?: string;
  updatedAt?: number | null;
  headerActions?: React.ReactNode;
  className?: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function BottomSheet({
  open,
  title,
  description,
  updatedAt,
  headerActions,
  className = '',
  onClose,
  children,
}: BottomSheetProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      const opener = document.activeElement;
      dialog.showModal();
      return () => {
        if (dialog.open) dialog.close();
        if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
      };
    }
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      className={`bottom-sheet ${className}`.trim()}
      ref={dialogRef}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onClose();
      }}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="bottom-sheet__handle" aria-hidden="true" />
      <header
        className={`bottom-sheet__header${updatedAt !== undefined ? ' bottom-sheet__header--updated' : ''}`}
      >
        <div>
          <h2 id={titleId}>{title}</h2>
          {description && updatedAt === undefined && <p>{description}</p>}
        </div>
        <SheetActions title={title} onClose={onClose}>
          {headerActions}
        </SheetActions>
        {updatedAt !== undefined && (
          <div className="bottom-sheet__subtitle">
            <p>{description}</p>
            <UpdatedAge timestamp={updatedAt} active={open} />
          </div>
        )}
      </header>
      <div className="bottom-sheet__content">{children}</div>
    </dialog>
  );
}

function SheetActions({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="bottom-sheet__actions">
      {children}
      <button className="icon-button" type="button" onClick={onClose} aria-label={`Close ${title}`}>
        ×
      </button>
    </div>
  );
}

function UpdatedAge({ timestamp, active }: { timestamp: number | null; active: boolean }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active || timestamp === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, timestamp]);
  const age = timestamp === null ? null : formatRelativeTime(timestamp, now);
  return (
    <span
      className="bottom-sheet__updated"
      title={
        timestamp === null
          ? 'No successful update yet'
          : new Date(timestamp * 1000).toLocaleString()
      }
    >
      <span aria-label={age === null ? 'No successful update yet' : `Updated ${age}`}>
        {age === null ? '—' : `~${age.replace(/ ago$/, '')}`}
      </span>
    </span>
  );
}
