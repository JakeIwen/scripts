import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

interface ToastValue {
  showToast: (message: string, tone?: 'normal' | 'error') => void;
}

const ToastContext = createContext<ToastValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<{ message: string; tone: 'normal' | 'error' } | null>(null);
  const [portalTarget, setPortalTarget] = useState<HTMLElement | null>(null);
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => {
    const updatePortalTarget = () => {
      const openDialogs = document.querySelectorAll<HTMLDialogElement>('dialog[open]');
      setPortalTarget(openDialogs.item(openDialogs.length - 1) || document.body);
    };
    updatePortalTarget();
    const observer = new MutationObserver(updatePortalTarget);
    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ['open'],
      subtree: true,
    });
    return () => observer.disconnect();
  }, []);

  const showToast = useCallback((message: string, tone: 'normal' | 'error' = 'normal') => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    setToast({ message, tone });
    timeoutRef.current = window.setTimeout(() => setToast(null), 3400);
  }, []);

  const value = useMemo(() => ({ showToast }), [showToast]);
  const toastElement = (
    <div
      className={`toast ${toast ? 'toast--visible' : ''} ${toast?.tone === 'error' ? 'toast--error' : ''}`}
      role="status"
      aria-live="polite"
    >
      {toast?.message ?? ''}
    </div>
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {portalTarget ? createPortal(toastElement, portalTarget) : toastElement}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside ToastProvider');
  return value;
}
