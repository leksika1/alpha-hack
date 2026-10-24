// Всплывающие уведомления в стилистике Альфа-Бизнеса: светлая карточка, статус-бейдж Core DS (как в ToastPlate), плавные появление и уход.
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';
import { CrossMIcon } from '@alfalab/icons-glyph/CrossMIcon';
import { StatusBadge, type StatusBadgeProps } from '@alfalab/core-components/status-badge';
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

const TONE_BADGE: Record<NonNullable<Toast['tone']>, StatusBadgeProps['view']> = {
  default: 'neutral-information',
  urgent: 'attention-alert',
  positive: 'positive-checkmark',
  error: 'negative-cross',
};

const LEAVE_MS = 220;
let seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<(Toast & { leaving?: boolean })[]>([]);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const dismiss = useCallback((id: number) => {
    setItems((xs) => xs.map((x) => (x.id === id ? { ...x, leaving: true } : x)));
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), LEAVE_MS);
  }, []);

  const show = useCallback(
    (t: Omit<Toast, 'id'>) => {
      // То же самое сообщение уже на экране — второй раз не показываем.
      if (itemsRef.current.some((x) => !x.leaving && x.title === t.title && x.body === t.body)) return;
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
        {items.map((t) => {
          return (
            <div
              key={t.id}
              className={cn(
                'flex items-start gap-3 rounded-tile border border-line bg-white p-4 shadow-pop',
                t.leaving ? 'motion-safe:animate-toast-out' : 'motion-safe:animate-toast-in',
              )}
            >
              {/* Бейдж и крестик — по центру первой строки заголовка: однострочное уведомление выровнено по центру. */}
              <span className="flex h-6 shrink-0 items-center">
                <StatusBadge view={TONE_BADGE[t.tone ?? 'default']} size={24} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="leading-6 font-semibold">{t.title}</div>
                {t.body && <div className="mt-0.5 line-clamp-3 text-sm text-ink-2">{t.body}</div>}
                {t.action && (
                  <button
                    className="pressable mt-2.5 rounded-[10px] bg-muted px-3 py-1.5 text-sm font-semibold hover:bg-muted-2"
                    onClick={() => {
                      t.action?.onClick();
                      dismiss(t.id);
                    }}
                  >
                    {t.action.label}
                  </button>
                )}
              </div>
              <button className="flex h-6 shrink-0 items-center text-ink-3 transition-colors hover:text-ink" aria-label="Закрыть" onClick={() => dismiss(t.id)}>
                <CrossMIcon width={18} height={18} />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}
