import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookmarkMIcon } from '@alfalab/icons-glyph/BookmarkMIcon';
import { BookmarkLineMIcon } from '@alfalab/icons-glyph/BookmarkLineMIcon';
import { EyeOffMIcon } from '@alfalab/icons-glyph/EyeOffMIcon';
import { EyeMIcon } from '@alfalab/icons-glyph/EyeMIcon';
import { trackEvent, useInsightState } from '../api/hooks';
import type { InsightCard } from '../api/types';
import { EVENT_TYPES, formatRelative, plural } from '../lib/format';
import { ImpactMeter, PolarityStatus, UrgentStatus } from './ui';
import { useToast } from './Toasts';

export function InsightCardView({ item, position, via = 'feed' }: { item: InsightCard; position: number; via?: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const state = useInsightState(item.insight_id);
  const ref = useRef<HTMLElement>(null);

  // card_impression — один раз, когда карточка попала в экран (метрика CJM 03).
  useEffect(() => {
    const el = ref.current;
    if (!el || item.state.seen) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          trackEvent('card_impression', item.company_id, item.insight_id, { position, via });
          obs.disconnect();
        }
      },
      { threshold: 0.6 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [item.company_id, item.insight_id, item.state.seen, position, via]);

  const open = () => navigate(`/analytics/insight/${item.insight_id}?via=${via}&position=${position}`);

  const act = (action: 'save' | 'unsave' | 'hide' | 'unhide') => {
    state.mutate(action, {
      onSuccess: () => {
        if (action === 'hide')
          toast.show({
            title: 'Инсайт скрыт',
            body: 'Вернуть его можно во вкладке «Скрытые».',
            action: { label: 'Вернуть', onClick: () => state.mutate('unhide') },
          });
        if (action === 'save') toast.show({ title: 'Сохранено', body: 'Инсайт доступен в фильтре «Сохранённые».', tone: 'positive', ttl: 3000 });
      },
    });
  };

  return (
    <article
      ref={ref}
      className={`insight-card ${item.state.seen ? '' : 'is-new'} ${item.state.hidden ? 'is-hidden' : ''}`}
      onClick={open}
      role="link"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && open()}
    >
      <div className="insight-card__top">
        {item.urgent && <UrgentStatus />}
        <PolarityStatus polarity={item.polarity as never} />
        {!item.state.opened && <span className="insight-card__new">Новое</span>}
        {item.state.updated && <span className="insight-card__updated">Обновлено</span>}
        <span className="insight-card__meta">
          {EVENT_TYPES[item.event_type] ?? item.event_type} · {formatRelative(item.published_at)}
        </span>
        <span className="insight-card__actions" onClick={(e) => e.stopPropagation()}>
          <button
            className="icon-btn icon-btn--sm"
            title={item.state.saved ? 'Убрать из сохранённых' : 'Сохранить'}
            aria-label={item.state.saved ? 'Убрать из сохранённых' : 'Сохранить'}
            onClick={() => act(item.state.saved ? 'unsave' : 'save')}
          >
            {item.state.saved ? <BookmarkMIcon className="is-saved" /> : <BookmarkLineMIcon />}
          </button>
          <button
            className="icon-btn icon-btn--sm"
            title={item.state.hidden ? 'Вернуть в ленту' : 'Скрыть — неактуально'}
            aria-label={item.state.hidden ? 'Вернуть в ленту' : 'Скрыть'}
            onClick={() => act(item.state.hidden ? 'unhide' : 'hide')}
          >
            {item.state.hidden ? <EyeMIcon /> : <EyeOffMIcon />}
          </button>
        </span>
      </div>

      <h3 className="insight-card__title">{item.title}</h3>
      <p className="insight-card__summary">{item.summary}</p>

      {item.why_for_you && (
        <div className="insight-card__why">
          <span className="insight-card__why-label">Почему это для вас</span>
          {item.why_for_you}
        </div>
      )}

      <div className="insight-card__bottom">
        <ImpactMeter level={item.impact_level as never} score={item.impact_score} />
        {!!item.priority_reasons?.length && (
          <span className="insight-card__reasons" title="Почему инсайт выше в ленте">
            Выше в ленте: {item.priority_reasons.join(' · ')}
          </span>
        )}
        <span className="insight-card__sources">
          {item.sources_count} {plural(item.sources_count, 'источник', 'источника', 'источников')}
          {item.bank_view_coverage !== 'none' && <span className="insight-card__bank">+ аналитика банка</span>}
        </span>
      </div>
    </article>
  );
}
