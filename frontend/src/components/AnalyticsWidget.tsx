// Постоянная точка входа на главной (CJM 01): 2–3 главных инсайта активной компании и «почему это для вас».
import { useEffect, useRef, type CSSProperties } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@alfalab/core-components/button';
import { Skeleton } from '@alfalab/core-components/skeleton';
import { ArrowRightMIcon } from '@alfalab/icons-glyph/ArrowRightMIcon';
import { trackEvent, useFeed, useFeedSummary } from '../api/hooks';
import { useSession } from '../auth/session';
import { cleanTitle, companyShortName, formatRelative, plural, POLARITY } from '../lib/format';
import { ImpactMeter, POLARITY_TEXT, sortInsights } from './ui';
import type { Polarity } from '../api/types';
import { SparkIcon } from '../layout/icons';
import { cn } from '../lib/cn';
import { useCountUp } from '../lib/useCountUp';

function Counter({ value, label, urgent = false }: { value: number | undefined; label: string; urgent?: boolean }) {
  const shown = useCountUp(value);
  return (
    <div className={cn('flex min-w-28 flex-col rounded-tile px-4 py-3 tabular-nums', urgent ? 'bg-neg-bg' : 'bg-muted')}>
      <span className={cn('text-[28px] leading-tight font-bold', urgent && 'text-accent')}>{shown ?? '—'}</span>
      <span className="text-[13px] text-ink-2">{label}</span>
    </div>
  );
}

export function AnalyticsWidget() {
  const { company, setCompanyId } = useSession();
  const navigate = useNavigate();
  const summary = useFeedSummary();
  const feed = useFeed(company?.company_id, 'all', '');
  const shownFor = useRef<string | null>(null);

  const companySummary = summary.data?.companies.find((c) => c.company_id === company?.company_id);
  const items = sortInsights(feed.data?.items ?? [], 'importance').slice(0, 3);
  const otherUrgent = (summary.data?.companies ?? []).filter((c) => c.company_id !== company?.company_id && c.urgent > 0);

  useEffect(() => {
    if (company && feed.data && shownFor.current !== company.company_id) {
      shownFor.current = company.company_id;
      trackEvent('widget_shown', company.company_id, items[0]?.insight_id, { items: items.length });
    }
  }, [company, feed.data, items]);

  const open = (insightId: string, position: number) => {
    if (company) trackEvent('widget_click', company.company_id, insightId, { position });
    navigate(`/analytics/insight/${insightId}?via=widget&position=${position}`);
  };

  return (
    <section
      className="relative mt-4 overflow-hidden rounded-3xl shadow-accent/20 bg-white p-6 shadow-card"
      aria-label="Аналитика для вашей компании"
    >
      <div className="mb-4 flex flex-col justify-between gap-6 md:flex-row">
        <div>
          <div className="eyebrow">
            <SparkIcon width={16} height={16} /> Альфа-Аналитика
          </div>
          <h2 className="mt-1 mb-1.5 text-2xl">Важное для вашего бизнеса</h2>
          <p className="max-w-155 text-ink-2">
            События из новостей и аналитики банка, отобранные по профилю компании: что произошло, почему это важно вам и что можно сделать.
          </p>
        </div>
        <div className="flex shrink-0 gap-3">
          <Counter value={companySummary?.unread} label={plural(companySummary?.unread ?? 0, 'новый инсайт', 'новых инсайта', 'новых инсайтов')} />
          <Counter value={companySummary?.urgent} label={plural(companySummary?.urgent ?? 0, 'срочный', 'срочных', 'срочных')} urgent />
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        {feed.isLoading &&
          [0, 1, 2].map((i) => (
            <div key={i} className="rounded-tile bg-muted p-4">
              <Skeleton visible animate>
                <div style={{ height: 76 }} />
              </Skeleton>
            </div>
          ))}
        {!feed.isLoading && items.length === 0 && (
          <div className="col-span-full py-3 text-ink-2">Пока нет значимых событий для этой компании — мы сообщим, когда они появятся.</div>
        )}
        {items.map((it, i) => {
          const polarity = (it.polarity as Polarity) ?? 'neutral';
          return (
            <button
              key={it.insight_id}
              style={{ '--i': i + 1 } as CSSProperties}
              className="stagger pressable group flex flex-col gap-1.5 rounded-tile bg-muted p-4 text-left hover:-translate-y-0.5 hover:bg-white hover:shadow-lift"
              onClick={() => open(it.insight_id, i + 1)}
            >
              <div className="flex items-center gap-2.5 text-xs text-ink-2">
                {it.urgent && <span className="font-semibold text-accent">Срочно</span>}
                <span className={cn('font-medium', POLARITY_TEXT[polarity])}>{POLARITY[polarity]?.label}</span>
                <span>{formatRelative(it.published_at)}</span>
                <span className="ml-auto">
                  <ImpactMeter level={it.impact_level as never} compact />
                </span>
              </div>
              <div className="line-clamp-3 text-[15px] leading-snug font-bold">{cleanTitle(it.title)}</div>
              {it.why_for_you && <div className="line-clamp-2 text-[13px] text-ink-2">{it.why_for_you}</div>}
            </button>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-5">
        <Button view="accent" size={48} rightAddons={<ArrowRightMIcon />} onClick={() => navigate('/analytics')}>
          Все инсайты компании
        </Button>
        {otherUrgent.length > 0 && (
          <span className="text-[13px] text-ink-2">
            Срочное в других компаниях:{' '}
            {otherUrgent.map((c, i) => (
              <span key={c.company_id}>
                {i > 0 && ', '}
                <Link to="/analytics?filter=urgent" onClick={() => setCompanyId(c.company_id)}>
                  {companyShortName(c.title)} ({c.urgent})
                </Link>
              </span>
            ))}
          </span>
        )}
      </div>
    </section>
  );
}
