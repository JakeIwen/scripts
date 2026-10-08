import { useEffect, useId, useRef } from 'react';

interface BottomSheetProps {
  open: boolean;
  title: string;
  description?: string;
  className?: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function BottomSheet({
  open,
  title,
  description,
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
      <header className="bottom-sheet__header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label={`Close ${title}`}
        >
          ×
        </button>
      </header>
      <div className="bottom-sheet__content">{children}</div>
    </dialog>
  );
}
