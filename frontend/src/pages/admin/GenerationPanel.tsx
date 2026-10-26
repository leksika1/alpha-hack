// Монитор генерации (`GET /admin/generation`): очередь, кандидаты по статусам, запуски за сутки, зависшие и упавшие;
// «Повторить» закрывает зависшие запуски и возвращает упавших кандидатов в очередь.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { get, post } from '../../api/client';
import type { GenerationOverview, GenerationRetryOut } from '../../api/types';
import { CardSkeleton, ErrorBox, H3, Note } from '../../components/ui';
import { formatDuration, formatRelative, ROLE_LABELS } from '../../lib/format';
import type { Role } from '../../api/types';

const CANDIDATE_STATUS: Record<string, string> = {
  pending: 'в очереди',
  generated: 'готовы',
  failed: 'упали',
  rejected: 'отклонены',
  skipped: 'пропущены',
};

const OUTCOME: Record<string, string> = {
  completed: 'готово',
  rejected: 'отклонено',
  skipped: 'пропущено',
  failed: 'ошибка',
  answered: 'ответ дан',
  running: 'идёт',
};

export function GenerationPanel() {
  const qc = useQueryClient();
  const overview = useQuery({ queryKey: ['admin-generation'], queryFn: () => get<GenerationOverview>('/admin/generation'), refetchInterval: 30_000 });
  const [includeRejected, setIncludeRejected] = useState(false);
  const retry = useMutation({
    mutationFn: () => post<GenerationRetryOut>('/admin/generation/retry', { company_ids: null, include_rejected: includeRejected }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin-generation'] });
      qc.invalidateQueries({ queryKey: ['admin-alerts'] });
    },
  });

  if (overview.isLoading) return <CardSkeleton rows={4} />;
  if (overview.isError) return <ErrorBox error={overview.error} onRetry={() => overview.refetch()} />;
  const g = overview.data!;
  const candidates = Object.entries(g.candidates as Record<string, number>);

  return (
    <section className="card p-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <H3 className="mb-0">Генерация инсайтов</H3>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-sm text-ink-2">
            <input type="checkbox" checked={includeRejected} onChange={(e) => setIncludeRejected(e.target.checked)} />
            вместе с отклонёнными
          </label>
          <Button view="secondary" size={32} loading={retry.isPending} onClick={() => retry.mutate()}>
            Повторить генерацию
          </Button>
        </div>
      </div>
      {retry.isError && <Note tone="warning">{retry.error.message}</Note>}
      {retry.data && (
        <Note>
          Закрыто зависших запусков: {retry.data.stuck_closed}, возвращено в очередь: {retry.data.requeued}, компаний: {retry.data.companies.length}.
        </Note>
      )}

      <div className="mb-4 flex flex-wrap gap-x-5 gap-y-2 text-sm">
        <span>
          Генератор: <b>{g.generator}</b>
        </span>
        <span>
          ML-сервис: <b>{g.ml_service}</b>
        </span>
        <span>
          Очередь: <b>{g.queue_length ?? '—'}</b>
        </span>
        {candidates.map(([status, n]) => (
          <span key={status}>
            Кандидаты {CANDIDATE_STATUS[status] ?? status}: <b>{n}</b>
          </span>
        ))}
      </div>

      <div className="mb-1 text-[13px] font-bold">Запуски агента за сутки</div>
      {g.runs_24h.length === 0 ? (
        <p className="mb-4 text-sm text-ink-2">Запусков не было.</p>
      ) : (
        <table className="mb-4 w-full text-sm tabular-nums">
          <thead className="text-left text-xs text-ink-2">
            <tr>
              <th className="py-1.5 font-normal">Тип</th>
              <th className="py-1.5 font-normal">Итог</th>
              <th className="py-1.5 text-right font-normal">Запусков</th>
              <th className="py-1.5 text-right font-normal">Медиана</th>
              <th className="py-1.5 text-right font-normal">95-й перцентиль</th>
            </tr>
          </thead>
          <tbody>
            {g.runs_24h.map((r) => (
              <tr key={`${r.kind}-${r.outcome}`} className="border-t border-line">
                <td className="py-1.5">{r.kind}</td>
                <td className="py-1.5">{OUTCOME[r.outcome] ?? r.outcome}</td>
                <td className="py-1.5 text-right">{r.count}</td>
                <td className="py-1.5 text-right">{formatDuration(r.p50_s)}</td>
                <td className="py-1.5 text-right">{formatDuration(r.p95_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="mb-1 text-[13px] font-bold">Зависшие запуски (дольше {formatDuration(g.stuck_after_s)})</div>
      {g.stuck_runs.length === 0 ? (
        <p className="mb-4 text-sm text-ink-2">Нет.</p>
      ) : (
        <ul className="mb-4 text-sm">
          {g.stuck_runs.map((r) => (
            <li key={r.id} className="border-b border-line py-1.5">
              {r.kind}, компания {r.company_id}, {r.generator}, запущен {formatRelative(r.started_at)}
            </li>
          ))}
        </ul>
      )}

      <div className="mb-1 text-[13px] font-bold">Последние ошибки</div>
      {g.recent_failures.length === 0 ? (
        <p className="text-sm text-ink-2">Нет.</p>
      ) : (
        <ul className="text-sm">
          {g.recent_failures.map((f) => (
            <li key={`${f.company_id}-${f.event_id}-${f.role}`} className="border-b border-line py-1.5">
              <div>
                Компания {f.company_id}, роль «{ROLE_LABELS[f.role as Role] ?? f.role}»: <b>{f.status === 'rejected' ? 'отклонён проверкой' : 'ошибка'}</b>{' '}
                <span className="ml-1.5 text-xs text-ink-2">{formatRelative(f.updated_at)}</span>
              </div>
              {f.status_reason && <div className="text-xs break-words text-ink-2">{f.status_reason}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
