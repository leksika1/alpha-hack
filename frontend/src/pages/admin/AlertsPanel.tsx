// Активные проблемы сервиса прямо сейчас (`GET /admin/alerts`): те же условия раз в 5 минут уходят в Telegram или лог.
import { useQuery } from '@tanstack/react-query';
import { get } from '../../api/client';
import type { Alert } from '../../api/types';
import { ErrorBox } from '../../components/ui';
import { cn } from '../../lib/cn';

export function useAlerts() {
  return useQuery({ queryKey: ['admin-alerts'], queryFn: () => get<Alert[]>('/admin/alerts'), refetchInterval: 60_000 });
}

export function AlertsPanel() {
  const alerts = useAlerts();
  if (alerts.isLoading) return null;
  if (alerts.isError) return <ErrorBox error={alerts.error} onRetry={() => alerts.refetch()} />;
  const items = alerts.data ?? [];

  if (items.length === 0) {
    return (
      <section className="card mb-4 flex items-center gap-2.5 px-6 py-4">
        <span className="size-2.5 rounded-full bg-pos-dot" />
        <span>Проблем нет: источники, бюджет LLM, генерация и ML-сервис в порядке.</span>
      </section>
    );
  }

  return (
    <section className="card mb-4 p-6" aria-label="Проблемы">
      <h3 className="mb-3 text-[17px]">
        Проблемы сейчас <span className="font-normal text-ink-3">{items.length}</span>
      </h3>
      <ul className="divide-y divide-line">
        {items.map((a) => (
          <li key={a.key} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
            <span className={cn('mt-[7px] size-2.5 shrink-0 rounded-full', a.severity === 'critical' ? 'bg-accent' : 'bg-att-dot')} />
            <div className="min-w-0">
              <div className="font-semibold">
                {a.title}{' '}
                <span className={cn('ml-1.5 text-xs font-medium', a.severity === 'critical' ? 'text-neg' : 'text-att')}>
                  {a.severity === 'critical' ? 'критично' : 'внимание'}
                </span>
              </div>
              <div className="text-sm text-ink-2">{a.detail}</div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
