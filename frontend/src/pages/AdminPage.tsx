// Панель администратора: north star и метрики этапов CJM, бюджет LLM, источники, ручная новость через общий конвейер.
import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { get } from '../api/client';
import { useManualNews } from '../api/hooks';
import type { ManualNewsOut } from '../api/types';
import { CardSkeleton, ErrorBox, H3, Note } from '../components/ui';
import { formatPercent, formatRelative } from '../lib/format';
import { cn } from '../lib/cn';

type Metric = { metric?: string; value?: number | null; [k: string]: unknown };
interface Metrics {
  since: string;
  until: string;
  sessions: number;
  users: number;
  north_star: Metric;
  speed: Record<string, number | null>;
  quality: Record<string, number | null>;
  return: Record<string, number | null>;
  cjm: Record<string, Metric>;
  generation: Record<string, unknown>;
}

interface SourceHealth {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  is_fixture: boolean;
  fallback_for: string | null;
  health_status: string;
  last_success_at: string | null;
  last_error: string | null;
  items_24h: number;
}

const STAGES: Record<string, string> = {
  '01_notice': '01 Заметить сервис',
  '02_context': '02 Подтвердить контекст',
  '03_select': '03 Выбрать важное',
  '04_understand': '04 Понять последствия',
  '05_verify': '05 Проверить основания',
  '06_clarify': '06 Уточнить сценарий',
  '07_act': '07 Выбрать действие',
  '08_return': '08 Оценить и вернуться',
};

const HEALTH_DOT: Record<string, string> = { healthy: 'bg-pos-dot', degraded: 'bg-att-dot', down: 'bg-accent' };

const fmt = (v: number | null | undefined) => (v == null ? '—' : formatPercent(v));

const KV = ({ children }: { children: ReactNode }) => <div className="mb-3 flex flex-wrap gap-x-5 gap-y-2 text-sm">{children}</div>;

export function AdminPage() {
  const metrics = useQuery({ queryKey: ['admin-metrics'], queryFn: () => get<Metrics>('/admin/metrics?days=7'), refetchInterval: 30_000 });
  const llm = useQuery({ queryKey: ['admin-llm'], queryFn: () => get<{ budget: Record<string, number | boolean | string> }>('/admin/llm-usage') });
  const sources = useQuery({ queryKey: ['admin-sources'], queryFn: () => get<SourceHealth[]>('/admin/sources/health') });

  return (
    <div>
      <h1 className="mt-2 mb-5 text-[28px]">Метрики сервиса «Альфа-Аналитика»</h1>
      {metrics.isLoading && <CardSkeleton rows={4} />}
      {metrics.isError && <ErrorBox error={metrics.error} onRetry={() => metrics.refetch()} />}
      {metrics.data && (
        <>
          <section className="rounded-card bg-night p-6 text-white">
            <div className="text-xs font-bold tracking-[0.06em] text-lime uppercase">North star · 7 дней</div>
            <div className="text-[44px] font-bold">{fmt(metrics.data.north_star.value)}</div>
            <div className="mb-1.5 text-base">{metrics.data.north_star.metric}</div>
            <div className="text-[13px] text-white/60">
              Сессий: {metrics.data.sessions} · пользователей: {metrics.data.users} · медиана до первого полезного инсайта:{' '}
              {metrics.data.speed.median_s_to_first_useful != null ? `${metrics.data.speed.median_s_to_first_useful} с` : '—'}
            </div>
          </section>
          <div className="my-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {Object.entries(metrics.data.cjm).map(([k, m]) => (
              <div key={k} className="card p-6">
                <div className="text-[13px] font-bold">{STAGES[k] ?? k}</div>
                <div className="my-1 text-[28px] font-bold">{fmt(m.value as number | null)}</div>
                <div className="text-[13px] text-ink-2">{m.metric}</div>
              </div>
            ))}
          </div>
          <section className="card p-6">
            <H3>Генерация инсайтов</H3>
            <KV>
              {Object.entries(metrics.data.generation)
                .filter(([, v]) => typeof v !== 'object')
                .map(([k, v]) => (
                  <span key={k}>
                    {k}: <b>{k.endsWith('share') ? fmt(v as number) : String(v)}</b>
                  </span>
                ))}
            </KV>
            <H3>Качество</H3>
            <KV>
              <span>
                Доля полезных: <b>{fmt(metrics.data.quality.useful_share)}</b>
              </span>
              <span>
                Полезных в топ-k: <b>{fmt(metrics.data.quality.top_k_useful_share)}</b>
              </span>
              <span>
                Ключевые выводы с источником: <b>{fmt(metrics.data.quality.key_statements_sourced)}</b>
              </span>
              <span>
                Отзывов: <b>{metrics.data.quality.feedback ?? 0}</b>
              </span>
            </KV>
          </section>
        </>
      )}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <ManualNews onDone={() => metrics.refetch()} />
        <section className="card p-6">
          <H3>Бюджет LLM на сегодня</H3>
          {llm.data && (
            <KV>
              <span>
                Лимит: <b>{String(llm.data.budget.limit_rub)} ₽</b>
              </span>
              <span>
                Потрачено: <b>{String(llm.data.budget.spent_rub)} ₽</b>
              </span>
              <span>
                Токены:{' '}
                <b>
                  {String(llm.data.budget.tokens_in)} / {String(llm.data.budget.tokens_out)}
                </b>
              </span>
              <span>{llm.data.budget.exhausted ? 'Бюджет исчерпан — генерация без LLM' : 'Бюджет доступен'}</span>
            </KV>
          )}
          <H3>Источники новостей</H3>
          <div>
            {sources.data?.map((s) => (
              <div key={s.id} className="grid grid-cols-[12px_1fr_auto] items-center gap-2.5 border-b border-line py-1.5 text-sm">
                <span className={cn('size-2.5 rounded-full', HEALTH_DOT[s.health_status] ?? 'bg-ink-3')} />
                <span>
                  {s.name}
                  {s.fallback_for && <span className="text-[13px] text-ink-2"> · резерв для {s.fallback_for}</span>}
                </span>
                <span className="text-[13px] text-ink-2">
                  {s.items_24h} за 24 ч{s.last_success_at ? ` · ${formatRelative(s.last_success_at)}` : ''}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="mb-2.5 flex flex-col gap-1.5 text-[13px] text-ink-2">
      <span>{label}</span>
      {children}
    </label>
  );
}

function ManualNews({ onDone }: { onDone: () => void }) {
  const send = useManualNews();
  const [title, setTitle] = useState('Банк России неожиданно повысил ключевую ставку до 16% годовых');
  const [body, setBody] = useState(
    'Совет директоров Банка России повысил ключевую ставку на 200 б.п., до 16% годовых. Регулятор указал на ускорение инфляции и рост кредитования. Решение повышает стоимость заёмного финансирования для компаний и банков.',
  );
  const [publisher, setPublisher] = useState('Ручной ввод (демо)');
  const [result, setResult] = useState<ManualNewsOut | null>(null);

  return (
    <section className="card p-6">
      <H3>Ручная новость через общий конвейер</H3>
      <p className="mb-3 text-[13px] text-ink-2">
        Проверка «новые свежие новости обрабатываются осмысленно»: новость проходит дедупликацию, теги, срочность, матчинг по всей базе и генерацию —
        пользователи получат уведомление.
      </p>
      <Field label="Заголовок">
        <input className="field-input" value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Текст">
        <textarea className="field-input" rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <Field label="Издатель">
        <input className="field-input" value={publisher} onChange={(e) => setPublisher(e.target.value)} />
      </Field>
      <Button
        view="accent"
        size={40}
        loading={send.isPending}
        onClick={() =>
          send.mutate([{ title, body, publisher, url: null, published_at: new Date().toISOString() }], {
            onSuccess: (r) => {
              setResult(r);
              onDone();
            },
          })
        }
      >
        Отправить в конвейер
      </Button>
      {send.isError && <p className="mt-2 text-[13px] text-neg">{send.error.message}</p>}
      {result && (
        <Note>
          Новых: {result.new}, дублей: {result.duplicates}, событий: {result.events.length}, срочных: {result.urgent_events.length}. Компаний в очереди на
          генерацию: {result.companies_queued.length}.
        </Note>
      )}
    </section>
  );
}
