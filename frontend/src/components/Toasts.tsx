import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { CrossMIcon } from '@alfalab/icons-glyph/CrossMIcon';
import { cn } from '../lib/cn';

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

const TONE_BORDER: Record<NonNullable<Toast['tone']>, string> = {
  default: '',
  urgent: 'border-l-4 border-accent',
  positive: 'border-l-4 border-pos-dot',
  error: 'border-l-4 border-[#ff4837]',
};

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
      <div className="fixed right-6 bottom-6 z-[1000] flex w-[380px] max-w-[calc(100vw-32px)] flex-col gap-2.5" role="status" aria-live="polite">
        {items.map((t) => (
          <div
            key={t.id}
            className={cn('flex animate-toast-in items-start gap-3 rounded-tile bg-[#1c1c1e] py-3.5 pr-3.5 pl-[18px] text-white shadow-[0_12px_40px_rgba(0,0,0,0.25)]', TONE_BORDER[t.tone ?? 'default'])}
          >
            <div className="min-w-0 flex-1">
              <div className="font-bold">{t.title}</div>
              {t.body && <div className="mt-0.5 line-clamp-3 text-sm text-white/75">{t.body}</div>}
              {t.action && (
                <button
                  className="mt-2 rounded-[10px] bg-white px-3 py-1.5 font-semibold text-night"
                  onClick={() => {
                    t.action?.onClick();
                    dismiss(t.id);
                  }}
                >
                  {t.action.label}
                </button>
              )}
            </div>
            <button className="p-0 text-white/60" aria-label="Закрыть" onClick={() => dismiss(t.id)}>
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
