// Строка ленты: только то, что нужно, чтобы решить, открывать ли инсайт, — заголовок, характер и сила влияния,
// одна строка «почему это вам». Подробности — в карточке инсайта.
import { useEffect, useRef, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookmarkMIcon } from '@alfalab/icons-glyph/BookmarkMIcon';
import { BookmarkLineMIcon } from '@alfalab/icons-glyph/BookmarkLineMIcon';
import { EyeOffMIcon } from '@alfalab/icons-glyph/EyeOffMIcon';
import { EyeMIcon } from '@alfalab/icons-glyph/EyeMIcon';
import { trackEvent, useInsightState, useTrack } from '../api/hooks';
import type { InsightCard, Polarity } from '../api/types';
import { cleanTitle, EVENT_TYPES, formatRelative, POLARITY } from '../lib/format';
import { ImpactMeter, POLARITY_TEXT } from './ui';
import { useToast } from './Toasts';
import { cn } from '../lib/cn';

const smallIconBtn =
  'inline-flex size-8 items-center justify-center rounded-full text-ink-2 transition hover:bg-muted-2 hover:text-ink active:scale-90 [&_svg]:size-5';

export function InsightCardView({ item, position, via = 'feed' }: { item: InsightCard; position: number; via?: string }) {
  const navigate = useNavigate();
  const toast = useToast();
  const state = useInsightState(item.insight_id);
  const track = useTrack(item.insight_id);
  const ref = useRef<HTMLElement>(null);
  const polarity = (item.polarity as Polarity) ?? 'neutral';

  // card_impression — один раз, когда строка попала в экран (метрика CJM 03).
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
    if (action === 'save' || action === 'unsave') {
      track.mutate(action === 'save', {
        onSuccess: () =>
          action === 'save' &&
          toast.show({ title: 'Отслеживается', body: 'Инсайт в фильтре «Отслеживаемые», об обновлениях события сообщим.', tone: 'positive', ttl: 3500 }),
      });
      return;
    }
    state.mutate(action, {
      onSuccess: () => {
        if (action === 'hide')
          toast.show({
            title: 'Инсайт скрыт',
            body: 'Вернуть его можно во вкладке «Скрытые».',
            action: { label: 'Вернуть', onClick: () => state.mutate('unhide') },
          });
      },
    });
  };

  return (
    <article
      ref={ref}
      style={{ '--i': Math.min(position, 12) } as CSSProperties}
      className={cn(
        'stagger group relative flex cursor-pointer gap-4 px-5 py-4 transition-colors duration-200 hover:bg-[#f8f8f9] focus-visible:bg-[#f8f8f9] focus-visible:outline-none',
        item.state.hidden && 'opacity-60',
      )}
      onClick={open}
      role="link"
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && open()}
    >
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex flex-wrap items-center gap-x-3 text-[13px] text-ink-2">
          {item.urgent && <span className="font-semibold text-accent">Срочно</span>}
          <span className={cn('font-medium', POLARITY_TEXT[polarity])}>{POLARITY[polarity]?.label}</span>
          <span>{EVENT_TYPES[item.event_type] ?? item.event_type}</span>
          <span>{formatRelative(item.published_at)}</span>
          {item.state.updated && <span className="font-medium text-att">Обновлено</span>}
          {/* Лента показывает не больше нескольких карточек одной макротемы — остальные свёрнуты (ADR-042). */}
          {item.more_in_topic > 0 && (
            <span className="rounded-md bg-muted-2 px-1.5 text-xs font-medium" title="Похожие события этой темы свёрнуты, чтобы не заполнять ленту">
              ещё {item.more_in_topic} по теме
            </span>
          )}
        </div>
        <h3 className="flex items-start gap-2 text-[17px] leading-snug">
          {!item.state.opened && <span className="mt-2 size-2 shrink-0 rounded-full bg-info" title="Не прочитано" />}
          <span className="line-clamp-2">{cleanTitle(item.title)}</span>
        </h3>
        {item.why_for_you && <p className="mt-1 mb-0 line-clamp-1 text-sm text-ink-2">{item.why_for_you}</p>}
      </div>

      <div className="flex shrink-0 flex-col items-end justify-between gap-2">
        <ImpactMeter level={item.impact_level as never} compact />
        <span
          className="flex gap-0.5 opacity-0 transition-opacity duration-200 group-focus-within:opacity-100 group-hover:opacity-100"
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className={smallIconBtn}
            title={item.state.saved ? 'Не отслеживать' : 'Отслеживать'}
            aria-label={item.state.saved ? 'Не отслеживать' : 'Отслеживать'}
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
    </article>
  );
}
