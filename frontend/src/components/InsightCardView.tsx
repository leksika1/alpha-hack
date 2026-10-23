import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookmarkMIcon } from '@alfalab/icons-glyph/BookmarkMIcon';
import { BookmarkLineMIcon } from '@alfalab/icons-glyph/BookmarkLineMIcon';
import { EyeOffMIcon } from '@alfalab/icons-glyph/EyeOffMIcon';
import { EyeMIcon } from '@alfalab/icons-glyph/EyeMIcon';
import { trackEvent, useInsightState } from '../api/hooks';
import type { InsightCard } from '../api/types';
import { EVENT_TYPES, formatRelative, plural } from '../lib/format';
import { Flag, ImpactMeter, PolarityStatus, UrgentStatus } from './ui';
import { useToast } from './Toasts';
import { cn } from '../lib/cn';

const smallIconBtn = 'inline-flex size-8 items-center justify-center rounded-full text-ink-2 hover:bg-muted-2 hover:text-ink [&_svg]:size-5';

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
      className={cn(
        'cursor-pointer rounded-card border border-transparent bg-white px-6 py-5 shadow-card transition hover:border-line hover:shadow-lift focus-visible:outline-2 focus-visible:outline-info',
        item.state.hidden && 'opacity-70',
      )}
      onClick={open}
      role="link"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && open()}
    >
      <div className="flex flex-wrap items-center gap-2">
        {item.urgent && <UrgentStatus />}
        <PolarityStatus polarity={item.polarity as never} />
        {!item.state.opened && <Flag tone="blue">Новое</Flag>}
        {item.state.updated && <Flag tone="orange">Обновлено</Flag>}
        <span className="text-[13px] text-ink-2">
          {EVENT_TYPES[item.event_type] ?? item.event_type} · {formatRelative(item.published_at)}
        </span>
        <span className="ml-auto flex gap-0.5" onClick={(e) => e.stopPropagation()}>
          <button
            className={smallIconBtn}
            title={item.state.saved ? 'Убрать из сохранённых' : 'Сохранить'}
            aria-label={item.state.saved ? 'Убрать из сохранённых' : 'Сохранить'}
            onClick={() => act(item.state.saved ? 'unsave' : 'save')}
          >
            {item.state.saved ? <BookmarkMIcon className="text-accent" /> : <BookmarkLineMIcon />}
          </button>
          <button
            className={smallIconBtn}
            title={item.state.hidden ? 'Вернуть в ленту' : 'Скрыть — неактуально'}
            aria-label={item.state.hidden ? 'Вернуть в ленту' : 'Скрыть'}
            onClick={() => act(item.state.hidden ? 'unhide' : 'hide')}
          >
            {item.state.hidden ? <EyeMIcon /> : <EyeOffMIcon />}
          </button>
        </span>
      </div>

      <h3 className="mt-2.5 mb-1.5 text-lg leading-snug">{item.title}</h3>
      <p className="line-clamp-2 text-ink-2">{item.summary}</p>

      {item.why_for_you && (
        <div className="my-3 rounded-xl bg-muted px-3.5 py-2.5 text-sm">
          <span className="mb-0.5 block text-xs font-bold text-accent">Почему это для вас</span>
          {item.why_for_you}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <ImpactMeter level={item.impact_level as never} score={item.impact_score} />
        {!!item.priority_reasons?.length && (
          <span className="text-[13px] text-ink-2" title="Почему инсайт выше в ленте">
            Выше в ленте: {item.priority_reasons.join(' · ')}
          </span>
        )}
        <span className="ml-auto text-[13px] text-ink-2">
          {item.sources_count} {plural(item.sources_count, 'источник', 'источника', 'источников')}
          {item.bank_view_coverage !== 'none' && <span className="ml-1.5 font-semibold text-accent">+ аналитика банка</span>}
        </span>
      </div>
    </article>
  );
}
