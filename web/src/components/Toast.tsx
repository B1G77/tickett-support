// One shared toast: success text in a polite live region (role="status"), errors in role="alert".
// Both regions stay mounted so screen readers notice when their text changes.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

type Toast = { text: string; error: boolean; n: number };
type Show = (text: string, error?: boolean) => void;

const ToastContext = createContext<Show>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<Toast | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const show = useCallback<Show>((text, error = false) => {
    clearTimeout(timer.current);
    setToast((t) => ({ text, error, n: (t?.n ?? 0) + 1 }));
    timer.current = setTimeout(() => setToast(null), error ? 9000 : 5000);
  }, []);

  useEffect(() => () => clearTimeout(timer.current), []);

  const close = () => setToast(null);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toast-dock">
        <div role="status" aria-live="polite">
          {toast && !toast.error && (
            <div key={toast.n} className="toast">
              <span>{toast.text}</span>
              <button type="button" className="toast-close" aria-label="Fechar" onClick={close}>×</button>
            </div>
          )}
        </div>
        <div role="alert">
          {toast?.error && (
            <div key={toast.n} className="toast toast-error">
              <span>{toast.text}</span>
              <button type="button" className="toast-close" aria-label="Fechar" onClick={close}>×</button>
            </div>
          )}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): Show {
  return useContext(ToastContext);
}
