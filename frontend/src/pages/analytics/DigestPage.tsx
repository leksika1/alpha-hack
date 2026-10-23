// Дайджест за день или неделю: плитки-счётчики работают как фильтры (по умолчанию — непрочитанные),
// ниже — все подходящие инсайты периода и следующие шаги.
import { useEffect, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { trackEvent, useDigest, useFeedAll } from '../../api/hooks';
import type { InsightCard, Polarity } from '../../api/types';
import { useSession } from '../../auth/session';
import { actionLabelClass, CardSkeleton, EmptyState, ErrorBox, H2, ImpactMeter, POLARITY_TEXT, Segmented, SortToggle, sortInsights, type SortMode } from '../../components/ui';
import { ACTION_TYPES, cleanTitle, formatDate, formatRelative, POLARITY } from '../../lib/format';
import { cn } from '../../lib/cn';
import { useCountUp } from '../../lib/useCountUp';

type TileKey = 'unread' | 'all' | 'urgent' | 'risks' | 'opportunities' | 'bank' | 'updated';

const TILES: { key: TileKey; label: string; empty: string; match: (it: InsightCard) => boolean }[] = [
  { key: 'unread', label: 'Непрочитанные', empty: 'Все инсайты периода прочитаны.', match: (it) => !it.state.opened },
  { key: 'all', label: 'Все за период', empty: 'За период событий нет.', match: () => true },
  { key: 'urgent', label: 'Срочные', empty: 'Срочных событий нет.', match: (it) => it.urgent },
  { key: 'risks', label: 'Риски', empty: 'Рисков за период нет.', match: (it) => it.polarity === 'risk' || it.polarity === 'mixed' },
  { key: 'opportunities', label: 'Возможности', empty: 'Возможностей за период нет.', match: (it) => it.polarity === 'opportunity' || it.polarity === 'mixed' },
  { key: 'bank', label: 'С аналитикой банка', empty: 'Инсайтов с аналитикой банка нет.', match: (it) => it.bank_view_coverage !== 'none' },
  { key: 'updated', label: 'Обновлённые', empty: 'Обновлённых инсайтов нет.', match: (it) => it.state.updated },
];

function CountUp({ value, className }: { value: number | undefined; className?: string }) {
  const shown = useCountUp(value);
  return <span className={className}>{shown ?? '—'}</span>;
}

function MiniInsight({ it, index }: { it: InsightCard; index: number }) {
  const polarity = (it.polarity as Polarity) ?? 'neutral';
  return (
    <Link
      to={`/analytics/insight/${it.insight_id}?via=digest`}
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
    </Link>
  );
}

export function DigestPage() {
  const { company } = useSession();
  const [period, setPeriod] = useState<'day' | 'week'>('week');
  const [tile, setTile] = useState<TileKey>('unread');
  const [sort, setSort] = useState<SortMode>('importance');
  const digest = useDigest(company?.company_id, period);
  const feed = useFeedAll(company?.company_id, period);

  useEffect(() => {
    if (company) trackEvent('digest_opened', company.company_id, null, { period });
  }, [company, period]);

  const all = feed.data ?? [];
  const active = TILES.find((t) => t.key === tile)!;
  const shown = sortInsights(all.filter(active.match), sort);

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
        {digest.data && (
          <span className="mb-3 text-ink-2">
            {formatDate(digest.data.since)} — {formatDate(digest.data.until)}
          </span>
        )}
      </div>

      {feed.isError && <ErrorBox error={feed.error} onRetry={() => feed.refetch()} />}

      <div className="mt-1 mb-6 grid grid-cols-2 gap-2.5 sm:grid-cols-4 xl:grid-cols-7" role="group" aria-label="Фильтр дайджеста">
        {TILES.map((t) => {
          const count = all.filter(t.match).length;
          const on = t.key === tile;
          return (
            <button
              key={t.key}
              type="button"
              aria-pressed={on}
              onClick={() => setTile(t.key)}
              className={cn(
                'pressable flex flex-col rounded-tile border-2 bg-white p-3.5 text-left shadow-card hover:-translate-y-0.5 hover:shadow-lift',
                on ? 'border-night' : 'border-transparent',
              )}
            >
              <CountUp value={feed.isLoading ? undefined : count} className={cn('text-[26px] font-bold tabular-nums', t.key === 'urgent' && count > 0 && 'text-accent')} />
              <span className="text-[13px] text-ink-2">{t.label}</span>
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <H2>
          {active.label} <span className="font-normal text-ink-3">{shown.length}</span>
        </H2>
        <SortToggle value={sort} onChange={setSort} />
      </div>
      {feed.isLoading && <CardSkeleton rows={3} />}
      {!feed.isLoading && shown.length === 0 && <EmptyState title="Пусто" text={active.empty} />}
      <div key={`${tile}-${period}-${sort}`} className={cn('mb-6 grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3 transition-opacity', feed.isFetching && !feed.isLoading && 'opacity-60')}>
        {shown.map((it, i) => (
          <MiniInsight key={it.insight_id} it={it} index={i + 1} />
        ))}
      </div>

      {!!digest.data?.next_steps.length && (
        <section className="card p-6">
          <H2>Следующие шаги</H2>
          <ul className="divide-y divide-line">
            {digest.data.next_steps.map((s, i) => (
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
