// Панель администратора: метрики CJM, проблемы, монитор генерации, источники, аналитика банка (PDF), бюджет и лимит LLM.
import { useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { Skeleton } from '@alfalab/core-components/skeleton';
import { get } from '../api/client';
import { useManualNews } from '../api/hooks';
import type { LlmProviderKey, ManualNewsOut } from '../api/types';
import { CardSkeleton, ErrorBox, H3, Note } from '../components/ui';
import { formatDate, formatDuration, formatNumber, formatPercent } from '../lib/format';
import { cn } from '../lib/cn';
import { AlertsPanel, useAlerts } from './admin/AlertsPanel';
import { GenerationPanel } from './admin/GenerationPanel';
import { DiscoverSource, SourcesHealth } from './admin/SourcesPanel';
import { AnalyticsPanel } from './admin/AnalyticsPanel';

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

const TABS = [
  { key: 'metrics', label: 'Метрики' },
  { key: 'alerts', label: 'Проблемы' },
  { key: 'generation', label: 'Генерация' },
  { key: 'sources', label: 'Источники' },
  { key: 'analytics', label: 'Аналитика банка' },
  { key: 'llm', label: 'LLM' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

const fmt = (v: number | null | undefined) => (v == null ? '—' : formatPercent(v));

const KV = ({ children }: { children: ReactNode }) => <div className="mb-3 flex flex-wrap gap-x-5 gap-y-2 text-sm">{children}</div>;

export function AdminPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : 'metrics';
  const alerts = useAlerts();
  const problems = alerts.data?.length ?? 0;

  return (
    <div>
      <h1 className="mt-2 mb-4 text-[28px]">Администрирование «Альфа-Аналитики»</h1>
      <nav className="mb-5 flex gap-1 overflow-x-auto border-b border-line" aria-label="Разделы администрирования">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            aria-current={tab === t.key ? 'page' : undefined}
            onClick={() => setParams(t.key === 'metrics' ? {} : { tab: t.key }, { replace: true })}
            className={cn(
              '-mb-px flex items-center gap-1.5 border-b-2 px-3.5 py-2.5 font-semibold whitespace-nowrap transition-colors duration-200 hover:text-ink',
              tab === t.key ? 'border-accent text-ink' : 'border-transparent text-ink-2',
            )}
          >
            {t.label}
            {t.key === 'alerts' && problems > 0 && (
              <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-[10px] bg-accent px-1.5 text-xs font-medium text-white">
                {problems}
              </span>
            )}
          </button>
        ))}
      </nav>

      <div key={tab} className="motion-safe:animate-fade-in">
        {tab === 'metrics' && <MetricsTab />}
        {tab === 'alerts' && <AlertsPanel />}
        {tab === 'generation' && <GenerationPanel />}
        {tab === 'sources' && (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="flex flex-col gap-4">
              <DiscoverSource />
              <ManualNews />
            </div>
            <section className="card self-start p-6">
              <SourcesHealth />
            </section>
          </div>
        )}
        {tab === 'analytics' && <AnalyticsPanel />}
        {tab === 'llm' && <LlmTab />}
      </div>
    </div>
  );
}

function MetricsTab() {
  const metrics = useQuery({ queryKey: ['admin-metrics'], queryFn: () => get<Metrics>('/admin/metrics?days=7'), refetchInterval: 30_000 });
  if (metrics.isLoading) return <CardSkeleton rows={4} />;
  if (metrics.isError) return <ErrorBox error={metrics.error} onRetry={() => metrics.refetch()} />;
  const m = metrics.data!;
  return (
    <>
      <section className="rounded-card bg-night p-6 text-white">
        <div className="text-xs font-bold tracking-[0.06em] text-lime uppercase">North star, 7 дней</div>
        <div className="text-[44px] font-bold">{fmt(m.north_star.value)}</div>
        <div className="mb-1.5 text-base">{m.north_star.metric}</div>
        <div className="text-[13px] text-white/60">
          Сессий: {m.sessions}, пользователей: {m.users}, медиана до первого полезного инсайта: {formatDuration(m.speed.median_s_to_first_useful)}
        </div>
      </section>
      <div className="my-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Object.entries(m.cjm).map(([k, c]) => (
          <div key={k} className="card p-6">
            <div className="text-[13px] font-bold">{STAGES[k] ?? k}</div>
            <div className="my-1 text-[28px] font-bold">{fmt(c.value as number | null)}</div>
            <div className="text-[13px] text-ink-2">{c.metric}</div>
          </div>
        ))}
      </div>
      <section className="card p-6">
        <H3>Генерация инсайтов</H3>
        <KV>
          {Object.entries(m.generation)
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
            Доля полезных: <b>{fmt(m.quality.useful_share)}</b>
          </span>
          <span>
            Полезных в топ-k: <b>{fmt(m.quality.top_k_useful_share)}</b>
          </span>
          <span>
            Ключевые выводы с источником: <b>{fmt(m.quality.key_statements_sourced)}</b>
          </span>
          <span>
            Отзывов: <b>{m.quality.feedback ?? 0}</b>
          </span>
        </KV>
      </section>
    </>
  );
}

const num = (v: unknown) => (typeof v === 'number' ? formatNumber(v) : String(v ?? '—'));

// Суммы провайдера — дробные (центы), округление до десятых их искажает.
const AMOUNT = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const amount = (v: number | null | undefined) => (v == null ? '—' : AMOUNT.format(v));

// Период лимита у провайдера в формате LiteLLM: «30s», «1h», «1d», «1mo».
const PERIOD_UNITS: Record<string, string> = { s: 'с', m: 'мин', h: 'ч', d: 'дн.', mo: 'мес.' };
function budgetPeriod(raw: string): string {
  const m = /^(\d+)\s*(mo|s|m|h|d)$/.exec(raw.trim());
  if (!m) return raw;
  if (m[1] === '1') return { s: 'секунда', m: 'минута', h: 'час', d: 'сутки', mo: 'месяц' }[m[2]] ?? raw;
  return `${m[1]} ${PERIOD_UNITS[m[2]]}`;
}

function LlmTab() {
  const llm = useQuery({ queryKey: ['admin-llm'], queryFn: () => get<{ budget: Record<string, number | boolean | string> }>('/admin/llm-usage') });
  const key = useQuery({ queryKey: ['admin-llm-provider'], queryFn: () => get<LlmProviderKey>('/admin/llm-provider') });
  const k = key.data;
  const used = k?.spend != null && k.max_budget ? Math.min(1, k.spend / k.max_budget) : null;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <section className="card p-6">
        <H3>Остаток лимита у провайдера</H3>
        {key.isLoading && (
          <Skeleton visible animate>
            <div style={{ height: 64 }} />
          </Skeleton>
        )}
        {key.isError && <ErrorBox error={key.error} onRetry={() => key.refetch()} />}
        {k?.status === 'not-supported' && <p className="text-ink-2">Провайдер не сообщает остаток лимита ключа.</p>}
        {k?.status === 'error' && <Note tone="warning">Провайдер не ответил: {k.error}</Note>}
        {k?.status === 'ok' && (
          <>
            <div className="text-[34px] leading-tight font-bold tabular-nums">{amount(k.remaining)}</div>
            <div className="mb-3 text-[13px] text-ink-2">осталось в единицах провайдера</div>
            {used != null && (
              <div
                className="mb-3 h-2 overflow-hidden rounded-full bg-muted-2"
                role="meter"
                aria-valuenow={Math.round(used * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className={cn('h-full rounded-full', used > 0.9 ? 'bg-accent' : used > 0.7 ? 'bg-att-dot' : 'bg-pos-dot')}
                  style={{ width: `${used * 100}%` }}
                />
              </div>
            )}
            <KV>
              <span>
                Потрачено: <b>{amount(k.spend)}</b>
              </span>
              <span>
                Лимит: <b>{amount(k.max_budget)}</b>
              </span>
              {k.budget_duration && (
                <span>
                  Период: <b>{budgetPeriod(k.budget_duration)}</b>
                </span>
              )}
              {k.budget_reset_at && (
                <span>
                  Сброс: <b>{formatDate(k.budget_reset_at, true)}</b>
                </span>
              )}
            </KV>
          </>
        )}
      </section>
      <section className="card p-6">
        <H3>Бюджет LLM на сегодня</H3>
        {llm.isError && <ErrorBox error={llm.error} onRetry={() => llm.refetch()} />}
        {llm.data && (
          <KV>
            <span>
              Лимит: <b>{num(llm.data.budget.limit_rub)} ₽</b>
            </span>
            <span>
              Потрачено: <b>{num(llm.data.budget.spent_rub)} ₽</b>
            </span>
            <span>
              Токены (вход / выход):{' '}
              <b>
                {num(llm.data.budget.tokens_in)} / {num(llm.data.budget.tokens_out)}
              </b>
            </span>
            <span>{llm.data.budget.exhausted ? 'Бюджет исчерпан — генерация без LLM' : 'Бюджет доступен'}</span>
          </KV>
        )}
      </section>
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

function ManualNews() {
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
            onSuccess: setResult,
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
