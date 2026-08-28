import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';

interface ToastValue {
  showToast: (message: string, tone?: 'normal' | 'error') => void;
}

const ToastContext = createContext<ToastValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<{ message: string; tone: 'normal' | 'error' } | null>(null);
  const timeoutRef = useRef<number | null>(null);

  const showToast = useCallback((message: string, tone: 'normal' | 'error' = 'normal') => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    setToast({ message, tone });
    timeoutRef.current = window.setTimeout(() => setToast(null), 3400);
  }, []);

  const value = useMemo(() => ({ showToast }), [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        className={`toast ${toast ? 'toast--visible' : ''} ${toast?.tone === 'error' ? 'toast--error' : ''}`}
        role="status"
        aria-live="polite"
      >
        {toast?.message ?? ''}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToast must be used inside ToastProvider');
  return value;
}
