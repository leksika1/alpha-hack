import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { CrossMIcon } from '@alfalab/icons-glyph/CrossMIcon';

export interface Toast {
  id: number;
  title: string;
  body?: string;
  tone?: 'default' | 'urgent' | 'positive' | 'error';
  action?: { label: string; onClick: () => void };
  ttl?: number;
}

interface ToastApi {
  show: (t: Omit<Toast, 'id'>) => void;
}

const ToastContext = createContext<ToastApi>({ show: () => undefined });

let seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);

  const show = useCallback(
    (t: Omit<Toast, 'id'>) => {
      const id = ++seq;
      setItems((xs) => [...xs.slice(-3), { ...t, id }]);
      setTimeout(() => dismiss(id), t.ttl ?? 7000);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast--${t.tone ?? 'default'}`}>
            <div className="toast__text">
              <div className="toast__title">{t.title}</div>
              {t.body && <div className="toast__body">{t.body}</div>}
              {t.action && (
                <button
                  className="toast__action"
                  onClick={() => {
                    t.action?.onClick();
                    dismiss(t.id);
                  }}
                >
                  {t.action.label}
                </button>
              )}
            </div>
            <button className="toast__close" aria-label="Закрыть" onClick={() => dismiss(t.id)}>
              <CrossMIcon width={18} height={18} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
