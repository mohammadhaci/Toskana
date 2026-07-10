import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

interface ToastItem {
  id: number;
  kind: "error" | "success";
  text: string;
}

interface ToastApi {
  error: (text: string) => void;
  success: (text: string) => void;
}

const ToastContext = createContext<ToastApi>({ error: () => {}, success: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const push = useCallback((kind: ToastItem["kind"], text: string) => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, kind, text }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4500);
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      error: (text) => push("error", text),
      success: (text) => push("success", text),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast ${toast.kind}`}>
            {toast.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
