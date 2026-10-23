// Живые уведомления (CJM 08): SSE /notifications/stream с переподключением по Last-Event-ID.
import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { streamSse } from '../api/sse';
import { keys } from '../api/hooks';
import type { Notification } from '../api/types';
import { useSession } from '../auth/session';
import { useToast } from './Toasts';

export function LiveNotifications() {
  const { isAuthenticated, setCompanyId } = useSession();
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const lastId = useRef<string | null>(null);
  const handlers = useRef({ toast, navigate, setCompanyId });
  handlers.current = { toast, navigate, setCompanyId };

  useEffect(() => {
    if (!isAuthenticated) return;
    let stopped = false;
    let ctrl = new AbortController();
    let wake: (() => void) | null = null;

    // Поток держит соединение на сервере, поэтому в скрытой вкладке закрываем его;
    // при возврате переподключаемся, пропущенное придёт по Last-Event-ID.
    const onVisibility = () => {
      if (document.hidden) ctrl.abort();
      else wake?.();
    };
    document.addEventListener('visibilitychange', onVisibility);
    const untilVisible = () =>
      document.hidden ? new Promise<void>((r) => (wake = () => ((wake = null), r()))) : Promise.resolve();

    const run = async () => {
      let backoff = 1000;
      while (!stopped) {
        await untilVisible();
        if (stopped) return;
        ctrl = new AbortController();
        try {
          await streamSse('/notifications/stream', {
            signal: ctrl.signal,
            headers: lastId.current ? { 'Last-Event-ID': lastId.current } : {},
            onMessage: (msg) => {
              backoff = 1000;
              if (msg.event === 'ready') {
                const data = JSON.parse(msg.data) as { last_id?: number | null };
                if (!lastId.current && data.last_id != null) lastId.current = String(data.last_id);
                return;
              }
              if (msg.event !== 'notification') return;
              if (msg.id) lastId.current = msg.id;
              const n = JSON.parse(msg.data) as Notification;
              qc.invalidateQueries({ queryKey: keys.notifications });
              qc.invalidateQueries({ queryKey: keys.summary });
              qc.invalidateQueries({ queryKey: keys.feedAll });
              if (n.kind === 'request_update') qc.invalidateQueries({ queryKey: keys.requests });
              const { toast: t, navigate: nav, setCompanyId: setCo } = handlers.current;
              t.show({
                title: n.title,
                body: n.body ?? undefined,
                tone: n.kind === 'urgent_insight' ? 'urgent' : 'default',
                ttl: 10000,
                action: n.insight_id
                  ? {
                      label: 'Открыть инсайт',
                      onClick: () => {
                        if (n.company_id) setCo(n.company_id);
                        nav(`/analytics/insight/${n.insight_id}?via=notification`);
                      },
                    }
                  : n.kind === 'request_update'
                    ? { label: 'Мои заявки', onClick: () => nav('/analytics/requests') }
                    : undefined,
              });
            },
          });
        } catch (e) {
          if (stopped) return;
          if (ctrl.signal.aborted) continue; // вкладку скрыли — ждём, пока её снова откроют
          if (e instanceof Error && 'status' in e && (e as { status: number }).status === 401) return;
        }
        if (stopped) return;
        await new Promise((r) => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, 30000);
      }
    };
    run();
    return () => {
      stopped = true;
      document.removeEventListener('visibilitychange', onVisibility);
      ctrl.abort();
      wake?.();
    };
  }, [isAuthenticated, qc]);

  return null;
}
