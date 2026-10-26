// Дайджест за день или неделю (CJM 08) по `GET /digest`: счётчики периода, главное, риски и возможности, следующие шаги.
import { useEffect, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { trackEvent, useDigest } from '../../api/hooks';
import type { Digest, InsightCard, Polarity } from '../../api/types';
import { useSession } from '../../auth/session';
import { actionLabelClass, CardSkeleton, EmptyState, ErrorBox, H2, ImpactMeter, POLARITY_TEXT, Segmented } from '../../components/ui';
import { ACTION_TYPES, cleanTitle, formatDate, formatRelative, POLARITY } from '../../lib/format';
import { cn } from '../../lib/cn';
import { useCountUp } from '../../lib/useCountUp';

type Period = 'day' | 'week';
type CountKey = keyof Digest['counts'];

const COUNTS: { key: CountKey; label: string; filter?: string }[] = [
  { key: 'new', label: 'Новые' },
  { key: 'unread', label: 'Непрочитанные', filter: 'unread' },
  { key: 'urgent', label: 'Срочные', filter: 'urgent' },
  { key: 'risks', label: 'Риски', filter: 'risks' },
  { key: 'opportunities', label: 'Возможности', filter: 'opportunities' },
  { key: 'with_bank_analytics', label: 'С аналитикой банка', filter: 'bank' },
  { key: 'updated', label: 'Обновлённые', filter: 'updated' },
];

function CountUp({ value, className }: { value: number | undefined; className?: string }) {
  const shown = useCountUp(value);
  return <span className={className}>{shown ?? '—'}</span>;
}

function MiniInsight({ it, index }: { it: InsightCard; index: number }) {
  const polarity = (it.polarity as Polarity) ?? 'neutral';
  return (
    <Link
      to={`/analytics/insight/${it.insight_id}?via=digest&position=${index}`}
      style={{ '--i': Math.min(index, 12) } as CSSProperties}
      className="stagger pressable flex flex-col gap-1.5 rounded-tile bg-white px-4 py-3.5 text-ink shadow-card hover:-translate-y-0.5 hover:no-underline hover:shadow-lift"
    >
      <div className="flex items-center gap-2.5 text-xs text-ink-2">
        {it.urgent && <span className="font-semibold text-accent">Срочно</span>}
        <span className={cn('font-medium', POLARITY_TEXT[polarity])}>{POLARITY[polarity]?.label}</span>
        <span>{formatRelative(it.published_at)}</span>
        <span className="ml-auto">
          <ImpactMeter level={it.impact_level as never} compact />
        </span>
      </div>
      <div className="flex items-start gap-2 leading-snug font-bold">
        {!it.state.opened && <span className="mt-[7px] size-2 shrink-0 rounded-full bg-info" title="Не прочитано" />}
        <span className="line-clamp-3">{cleanTitle(it.title)}</span>
      </div>
      {it.why_for_you && <p className="m-0 line-clamp-2 text-[13px] text-ink-2">{it.why_for_you}</p>}
    </Link>
  );
}

function Section({ title, items, empty }: { title: string; items: InsightCard[]; empty: string }) {
  return (
    <section className="mb-6">
      <H2>
        {title} <span className="font-normal text-ink-3">{items.length}</span>
      </H2>
      {items.length === 0 ? (
        <p className="text-ink-2">{empty}</p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
          {items.map((it, i) => (
            <MiniInsight key={it.insight_id} it={it} index={i + 1} />
          ))}
        </div>
      )}
    </section>
  );
}

export function DigestPage() {
  const { company } = useSession();
  const [period, setPeriod] = useState<Period>('day');
  const digest = useDigest(company?.company_id, period);
  const d = digest.data;

  useEffect(() => {
    if (company) trackEvent('digest_opened', company.company_id, null, { period });
  }, [company, period]);

  const empty = d && d.top.length === 0 && d.risks.length === 0 && d.opportunities.length === 0;

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Период дайджеста"
          value={period}
          onChange={setPeriod}
          options={[
            { key: 'day', label: 'За день' },
            { key: 'week', label: 'За неделю' },
          ]}
        />
        {d && (
          <span className="mb-3 text-ink-2">
            {formatDate(d.since, true)} — {formatDate(d.until, true)}
          </span>
        )}
      </div>

      {digest.isError && <ErrorBox error={digest.error} onRetry={() => digest.refetch()} />}

      <div className="mt-1 mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7" aria-label="Итоги периода">
        {COUNTS.map((c) => {
          const value = d?.counts[c.key];
          const body = (
            <>
              <CountUp
                value={digest.isLoading ? undefined : value}
                className={cn('text-[22px] leading-tight font-bold tabular-nums', c.key === 'urgent' && !!value && 'text-accent')}
              />
              <span className="text-[13px] text-ink-2">{c.label}</span>
            </>
          );
          const tile = 'flex flex-col rounded-tile bg-white px-3.5 py-2.5 text-left text-ink shadow-card';
          // Плитка ведёт в ленту с тем же фильтром и периодом.
          return c.filter ? (
            <Link
              key={c.key}
              to={`/analytics?filter=${c.filter}&period=${period}`}
              className={cn(tile, 'pressable hover:-translate-y-0.5 hover:no-underline hover:shadow-lift')}
            >
              {body}
            </Link>
          ) : (
            <div key={c.key} className={tile}>
              {body}
            </div>
          );
        })}
      </div>

      {digest.isLoading && <CardSkeleton rows={3} />}
      {empty && <EmptyState title="За период ничего нового" text="Значимых событий для компании не было. Новости проверяются каждые 15 минут." />}

      {d && !empty && (
        <div key={period} className="motion-safe:animate-fade-in">
          <Section title="Главное" items={d.top} empty="Главных событий за период нет." />
          <Section title="Риски" items={d.risks} empty="Рисков за период не найдено." />
          <Section title="Возможности" items={d.opportunities} empty="Возможностей за период не найдено." />
        </div>
      )}

      {!!d?.next_steps.length && (
        <section className="card p-6">
          <H2>Следующие шаги</H2>
          <ul className="divide-y divide-line">
            {d.next_steps.map((s, i) => (
              <li key={i} className="py-3 first:pt-0 last:pb-0">
                <div className={actionLabelClass(s.type)}>{ACTION_TYPES[s.type] ?? s.type}</div>
                <div>{s.text}</div>
                <Link className="text-[13px]" to={`/analytics/insight/${s.insight_id}?via=digest`}>
                  {cleanTitle(s.insight_title)}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
