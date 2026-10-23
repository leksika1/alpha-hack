// Постоянная точка входа на главной (CJM 01): 2–3 главных инсайта активной компании и «почему это для вас».
import { useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@alfalab/core-components/button';
import { Skeleton } from '@alfalab/core-components/skeleton';
import { ArrowRightMIcon } from '@alfalab/icons-glyph/ArrowRightMIcon';
import { trackEvent, useFeed, useFeedSummary } from '../api/hooks';
import { useSession } from '../auth/session';
import { companyShortName, EVENT_TYPES, formatRelative, plural } from '../lib/format';
import { ImpactMeter, PolarityStatus, UrgentStatus } from './ui';
import { SparkIcon } from '../layout/icons';

export function AnalyticsWidget() {
  const { company, setCompanyId } = useSession();
  const navigate = useNavigate();
  const summary = useFeedSummary();
  const feed = useFeed(company?.company_id, 'all', '');
  const shownFor = useRef<string | null>(null);

  const companySummary = summary.data?.companies.find((c) => c.company_id === company?.company_id);
  const items = (feed.data?.items ?? []).slice(0, 3);
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
    <section className="widget" aria-label="Аналитика для вашей компании">
      <div className="widget__head">
        <div>
          <div className="widget__eyebrow">
            <SparkIcon width={16} height={16} /> Альфа-Аналитика
          </div>
          <h2 className="widget__title">Важное для вашего бизнеса</h2>
          <p className="widget__lead">
            События из новостей и аналитики банка, отобранные по профилю компании: что произошло, почему это важно вам и что
            можно сделать.
          </p>
        </div>
        <div className="widget__counters">
          <div className="widget__counter">
            <span className="widget__counter-value">{companySummary?.unread ?? '—'}</span>
            <span className="widget__counter-label">{plural(companySummary?.unread ?? 0, 'новый инсайт', 'новых инсайта', 'новых инсайтов')}</span>
          </div>
          <div className="widget__counter widget__counter--urgent">
            <span className="widget__counter-value">{companySummary?.urgent ?? '—'}</span>
            <span className="widget__counter-label">{plural(companySummary?.urgent ?? 0, 'срочный', 'срочных', 'срочных')}</span>
          </div>
        </div>
      </div>

      <div className="widget__list">
        {feed.isLoading &&
          [0, 1, 2].map((i) => (
            <div className="widget__item" key={i}>
              <Skeleton visible animate colors="inverted">
                <div style={{ height: 76 }} />
              </Skeleton>
            </div>
          ))}
        {!feed.isLoading && items.length === 0 && (
          <div className="widget__empty">Пока нет значимых событий для этой компании — мы сообщим, когда они появятся.</div>
        )}
        {items.map((it, i) => (
          <button key={it.insight_id} className="widget__item" onClick={() => open(it.insight_id, i + 1)}>
            <div className="widget__item-top">
              {it.urgent && <UrgentStatus />}
              <PolarityStatus polarity={it.polarity as never} />
              <span className="widget__meta">
                {EVENT_TYPES[it.event_type] ?? it.event_type} · {formatRelative(it.published_at)}
              </span>
              <ImpactMeter level={it.impact_level as never} compact />
            </div>
            <div className="widget__item-title">{it.title}</div>
            {it.why_for_you && (
              <div className="widget__why">
                <span>Почему это для вас:</span> {it.why_for_you}
              </div>
            )}
          </button>
        ))}
      </div>

      <div className="widget__foot">
        <Button view="accent" size={48} rightAddons={<ArrowRightMIcon />} onClick={() => navigate('/analytics')}>
          Все инсайты компании
        </Button>
        {otherUrgent.length > 0 && (
          <span className="widget__other">
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
