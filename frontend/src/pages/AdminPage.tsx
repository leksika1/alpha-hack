// Панель администратора: north star и метрики этапов CJM, бюджет LLM, источники, ручная новость через общий конвейер.
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { get } from '../api/client';
import { useManualNews } from '../api/hooks';
import type { ManualNewsOut } from '../api/types';
import { CardSkeleton, ErrorBox } from '../components/ui';
import { formatPercent, formatRelative } from '../lib/format';

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

const fmt = (v: number | null | undefined) => (v == null ? '—' : formatPercent(v));

export function AdminPage() {
  const metrics = useQuery({ queryKey: ['admin-metrics'], queryFn: () => get<Metrics>('/admin/metrics?days=7'), refetchInterval: 30_000 });
  const llm = useQuery({ queryKey: ['admin-llm'], queryFn: () => get<{ budget: Record<string, number | boolean | string> }>('/admin/llm-usage') });
  const sources = useQuery({ queryKey: ['admin-sources'], queryFn: () => get<SourceHealth[]>('/admin/sources/health') });

  return (
    <div className="admin">
      <h1 className="page-title">Метрики сервиса «Альфа-Аналитика»</h1>
      {metrics.isLoading && <CardSkeleton rows={4} />}
      {metrics.isError && <ErrorBox error={metrics.error} onRetry={() => metrics.refetch()} />}
      {metrics.data && (
        <>
          <section className="card card--pad north">
            <div className="north__label">North star · 7 дней</div>
            <div className="north__value">{fmt(metrics.data.north_star.value)}</div>
            <div className="north__text">{metrics.data.north_star.metric}</div>
            <div className="muted small">
              Сессий: {metrics.data.sessions} · пользователей: {metrics.data.users} · медиана до первого полезного инсайта:{' '}
              {metrics.data.speed.median_s_to_first_useful != null ? `${metrics.data.speed.median_s_to_first_useful} с` : '—'}
            </div>
          </section>
          <div className="cjm-grid">
            {Object.entries(metrics.data.cjm).map(([k, m]) => (
              <div key={k} className="card card--pad cjm-card">
                <div className="cjm-card__stage">{STAGES[k] ?? k}</div>
                <div className="cjm-card__value">{fmt(m.value as number | null)}</div>
                <div className="muted small">{m.metric}</div>
              </div>
            ))}
          </div>
          <section className="card card--pad">
            <h3 className="h3">Генерация инсайтов</h3>
            <div className="kv">
              {Object.entries(metrics.data.generation)
                .filter(([, v]) => typeof v !== 'object')
                .map(([k, v]) => (
                  <span key={k}>
                    {k}: <b>{k.endsWith('share') ? fmt(v as number) : String(v)}</b>
                  </span>
                ))}
            </div>
            <h3 className="h3">Качество</h3>
            <div className="kv">
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
            </div>
          </section>
        </>
      )}

      <div className="admin__cols">
        <ManualNews onDone={() => metrics.refetch()} />
        <section className="card card--pad">
          <h3 className="h3">Бюджет LLM на сегодня</h3>
          {llm.data && (
            <div className="kv">
              <span>
                Лимит: <b>{String(llm.data.budget.limit_rub)} ₽</b>
              </span>
              <span>
                Потрачено: <b>{String(llm.data.budget.spent_rub)} ₽</b>
              </span>
              <span>
                Токены: <b>{String(llm.data.budget.tokens_in)} / {String(llm.data.budget.tokens_out)}</b>
              </span>
              <span>{llm.data.budget.exhausted ? 'Бюджет исчерпан — генерация без LLM' : 'Бюджет доступен'}</span>
            </div>
          )}
          <h3 className="h3">Источники новостей</h3>
          <div className="sources-health">
            {sources.data?.map((s) => (
              <div key={s.id} className="sources-health__row">
                <span className={`dot-status dot-status--${s.health_status}`} />
                <span className="sources-health__name">
                  {s.name}
                  {s.fallback_for && <span className="muted small"> · резерв для {s.fallback_for}</span>}
                </span>
                <span className="muted small">
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

function ManualNews({ onDone }: { onDone: () => void }) {
  const send = useManualNews();
  const [title, setTitle] = useState('Банк России неожиданно повысил ключевую ставку до 16% годовых');
  const [body, setBody] = useState(
    'Совет директоров Банка России повысил ключевую ставку на 200 б.п., до 16% годовых. Регулятор указал на ускорение инфляции и рост кредитования. Решение повышает стоимость заёмного финансирования для компаний и банков.',
  );
  const [publisher, setPublisher] = useState('Ручной ввод (демо)');
  const [result, setResult] = useState<ManualNewsOut | null>(null);

  return (
    <section className="card card--pad">
      <h3 className="h3">Ручная новость через общий конвейер</h3>
      <p className="muted small">
        Проверка «новые свежие новости обрабатываются осмысленно»: новость проходит дедупликацию, теги, срочность, матчинг по всей базе и
        генерацию — пользователи получат уведомление.
      </p>
      <label className="field">
        <span>Заголовок</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field">
        <span>Текст</span>
        <textarea rows={4} value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      <label className="field">
        <span>Издатель</span>
        <input value={publisher} onChange={(e) => setPublisher(e.target.value)} />
      </label>
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
      {send.isError && <p className="text-negative small">{send.error.message}</p>}
      {result && (
        <div className="note note--info">
          Новых: {result.new}, дублей: {result.duplicates}, событий: {result.events.length}, срочных: {result.urgent_events.length}. Компаний в очереди на
          генерацию: {result.companies_queued.length}.
        </div>
      )}
    </section>
  );
}
