// Дайджест за день или неделю: счётчики, главное, риски, возможности и следующие шаги.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { trackEvent, useDigest } from '../../api/hooks';
import type { InsightCard } from '../../api/types';
import { useSession } from '../../auth/session';
import { CardSkeleton, EmptyState, ErrorBox, ImpactMeter, PolarityStatus } from '../../components/ui';
import { ACTION_TYPES, formatDate } from '../../lib/format';

function MiniInsight({ it, via }: { it: InsightCard; via: string }) {
  return (
    <Link to={`/analytics/insight/${it.insight_id}?via=${via}`} className="mini-insight">
      <div className="mini-insight__top">
        <PolarityStatus polarity={it.polarity as never} />
        <ImpactMeter level={it.impact_level as never} compact />
      </div>
      <div className="mini-insight__title">{it.title}</div>
      {it.why_for_you && <div className="mini-insight__why">{it.why_for_you}</div>}
    </Link>
  );
}

export function DigestPage() {
  const { company } = useSession();
  const [period, setPeriod] = useState<'day' | 'week'>('week');
  const digest = useDigest(company?.company_id, period);

  useEffect(() => {
    if (company) trackEvent('digest_opened', company.company_id, null, { period });
  }, [company, period]);

  const d = digest.data;
  const counters = d
    ? [
        ['Новых', d.counts.new],
        ['Срочных', d.counts.urgent],
        ['Рисков', d.counts.risks],
        ['Возможностей', d.counts.opportunities],
        ['С аналитикой банка', d.counts.with_bank_analytics],
        ['Обновлено', d.counts.updated],
      ]
    : [];

  return (
    <div className="digest">
      <div className="segmented" role="group" aria-label="Период дайджеста">
        <button className={period === 'day' ? 'is-active' : ''} onClick={() => setPeriod('day')}>
          За день
        </button>
        <button className={period === 'week' ? 'is-active' : ''} onClick={() => setPeriod('week')}>
          За неделю
        </button>
      </div>

      {digest.isLoading && <CardSkeleton rows={4} />}
      {digest.isError && <ErrorBox error={digest.error} onRetry={() => digest.refetch()} />}
      {d && (
        <>
          <p className="muted">
            {formatDate(d.since)} — {formatDate(d.until)}
          </p>
          <div className="counters">
            {counters.map(([label, value]) => (
              <div key={label} className="counter">
                <span className="counter__value">{value}</span>
                <span className="counter__label">{label}</span>
              </div>
            ))}
          </div>

          {d.top.length === 0 ? (
            <EmptyState title="За период событий нет" text="Попробуйте выбрать неделю или загляните в ленту позже." />
          ) : (
            <>
              <h2 className="h2">Главное</h2>
              <div className="mini-grid">
                {d.top.map((it) => (
                  <MiniInsight key={it.insight_id} it={it} via="digest" />
                ))}
              </div>
              <div className="digest__cols">
                <div>
                  <h2 className="h2">Риски</h2>
                  {d.risks.length ? d.risks.map((it) => <MiniInsight key={it.insight_id} it={it} via="digest" />) : <p className="muted">Нет</p>}
                </div>
                <div>
                  <h2 className="h2">Возможности</h2>
                  {d.opportunities.length ? (
                    d.opportunities.map((it) => <MiniInsight key={it.insight_id} it={it} via="digest" />)
                  ) : (
                    <p className="muted">Нет</p>
                  )}
                </div>
              </div>
              {d.next_steps.length > 0 && (
                <section className="card card--pad">
                  <h2 className="h2">Следующие шаги</h2>
                  <ul className="next-steps">
                    {d.next_steps.map((s, i) => (
                      <li key={i}>
                        <span className={`actions-list__type actions-list__type--${s.type}`}>{ACTION_TYPES[s.type] ?? s.type}</span>
                        <div>
                          <div>{s.text}</div>
                          <Link className="small" to={`/analytics/insight/${s.insight_id}?via=digest`}>
                            {s.insight_title}
                          </Link>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
