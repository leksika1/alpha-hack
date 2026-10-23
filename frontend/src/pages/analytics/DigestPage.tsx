// Дайджест за день или неделю: счётчики, главное, риски, возможности и следующие шаги.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { trackEvent, useDigest } from '../../api/hooks';
import type { InsightCard } from '../../api/types';
import { useSession } from '../../auth/session';
import { actionTypeClass, CardSkeleton, EmptyState, ErrorBox, H2, ImpactMeter, PolarityStatus, Segmented } from '../../components/ui';
import { ACTION_TYPES, formatDate } from '../../lib/format';
import { cn } from '../../lib/cn';

function MiniInsight({ it, via, className }: { it: InsightCard; via: string; className?: string }) {
  return (
    <Link
      to={`/analytics/insight/${it.insight_id}?via=${via}`}
      className={cn('flex flex-col gap-1.5 rounded-tile bg-white px-4 py-3.5 text-ink shadow-card hover:no-underline hover:shadow-lift', className)}
    >
      <div className="flex items-center justify-between">
        <PolarityStatus polarity={it.polarity as never} />
        <ImpactMeter level={it.impact_level as never} compact />
      </div>
      <div className="leading-snug font-bold">{it.title}</div>
      {it.why_for_you && <div className="line-clamp-2 text-[13px] text-ink-2">{it.why_for_you}</div>}
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
    <div>
      <Segmented
        label="Период дайджеста"
        value={period}
        onChange={setPeriod}
        options={[
          { key: 'day', label: 'За день' },
          { key: 'week', label: 'За неделю' },
        ]}
      />

      {digest.isLoading && <CardSkeleton rows={4} />}
      {digest.isError && <ErrorBox error={digest.error} onRetry={() => digest.refetch()} />}
      {d && (
        <>
          <p className="text-ink-2">
            {formatDate(d.since)} — {formatDate(d.until)}
          </p>
          <div className="mt-2 mb-5 grid grid-cols-3 gap-2.5 lg:grid-cols-6">
            {counters.map(([label, value]) => (
              <div key={label} className="flex flex-col rounded-tile bg-white p-3.5 shadow-card">
                <span className="text-[26px] font-bold">{value}</span>
                <span className="text-[13px] text-ink-2">{label}</span>
              </div>
            ))}
          </div>

          {d.top.length === 0 ? (
            <EmptyState title="За период событий нет" text="Попробуйте выбрать неделю или загляните в ленту позже." />
          ) : (
            <>
              <H2>Главное</H2>
              <div className="mb-5 grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
                {d.top.map((it) => (
                  <MiniInsight key={it.insight_id} it={it} via="digest" />
                ))}
              </div>
              <div className="mb-4 grid gap-4 md:grid-cols-2">
                <div>
                  <H2>Риски</H2>
                  {d.risks.length ? d.risks.map((it) => <MiniInsight key={it.insight_id} it={it} via="digest" className="mb-2.5" />) : <p className="text-ink-2">Нет</p>}
                </div>
                <div>
                  <H2>Возможности</H2>
                  {d.opportunities.length ? (
                    d.opportunities.map((it) => <MiniInsight key={it.insight_id} it={it} via="digest" className="mb-2.5" />)
                  ) : (
                    <p className="text-ink-2">Нет</p>
                  )}
                </div>
              </div>
              {d.next_steps.length > 0 && (
                <section className="card p-6">
                  <H2>Следующие шаги</H2>
                  <ul>
                    {d.next_steps.map((s, i) => (
                      <li key={i} className="flex items-start gap-3 border-b border-line py-2.5 last:border-b-0">
                        <span className={actionTypeClass(s.type)}>{ACTION_TYPES[s.type] ?? s.type}</span>
                        <div>
                          <div>{s.text}</div>
                          <Link className="text-[13px]" to={`/analytics/insight/${s.insight_id}?via=digest`}>
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
