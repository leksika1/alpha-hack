// Лента инсайтов (CJM 03): ранжирование по значимости для компании и роли, период.
// Над лентой — счётчики за период (бывший дайджест): каждая плитка работает как фильтр ленты.
import { useSearchParams } from 'react-router-dom';
import { Tag } from '@alfalab/core-components/tag';
import { useFeedAll } from '../../api/hooks';
import type { FeedFilter, InsightCard } from '../../api/types';
import { useSession } from '../../auth/session';
import { InsightCardView } from '../../components/InsightCardView';
import { CardSkeleton, EmptyState, ErrorBox, Select, SortToggle, sortInsights, type SortMode } from '../../components/ui';
import { cn } from '../../lib/cn';
import { useCountUp } from '../../lib/useCountUp';

// Плитки фильтруют ленту «все» на клиенте — так число на плитке всегда совпадает с длиной списка.
type TileKey = 'all' | 'unread' | 'urgent' | 'risks' | 'opportunities' | 'bank' | 'updated';
// Отслеживаемые и скрытые — отдельные выборки сервера, к ленте «все» не относятся.
type ListKey = Extract<FeedFilter, 'saved' | 'hidden'>;

const TILES: { key: TileKey; label: string; empty: string; match: (it: InsightCard) => boolean }[] = [
  { key: 'all', label: 'Все', empty: 'Значимых событий для компании пока нет. Новости проверяются каждые 15 минут.', match: () => true },
  { key: 'unread', label: 'Непрочитанные', empty: 'Все инсайты за период прочитаны.', match: (it) => !it.state.opened },
  { key: 'urgent', label: 'Срочные', empty: 'Срочных событий нет — можно выдохнуть.', match: (it) => it.urgent },
  { key: 'risks', label: 'Риски', empty: 'Рисков за выбранный период не найдено.', match: (it) => it.polarity === 'risk' || it.polarity === 'mixed' },
  {
    key: 'opportunities',
    label: 'Возможности',
    empty: 'Возможностей за выбранный период не найдено.',
    match: (it) => it.polarity === 'opportunity' || it.polarity === 'mixed',
  },
  { key: 'bank', label: 'С аналитикой банка', empty: 'Инсайтов с аналитикой банка за период нет.', match: (it) => it.bank_view_coverage !== 'none' },
  { key: 'updated', label: 'Обновлённые', empty: 'Обновлённых инсайтов нет.', match: (it) => it.state.updated },
];

const LISTS: { key: ListKey; label: string; empty: string }[] = [
  { key: 'saved', label: 'Отслеживаемые', empty: 'Отмечайте инсайты значком закладки — они появятся здесь, а об обновлениях событий придут уведомления.' },
  { key: 'hidden', label: 'Скрытые', empty: 'Скрытых инсайтов нет.' },
];

const PERIODS = [
  { key: 'all', label: 'За всё время' },
  { key: 'day', label: 'За сутки' },
  { key: 'week', label: 'За неделю' },
  { key: 'month', label: 'За месяц' },
];

function CountUp({ value, className }: { value: number | undefined; className?: string }) {
  const shown = useCountUp(value);
  return <span className={className}>{shown ?? '—'}</span>;
}

export function FeedPage() {
  const { company } = useSession();
  const [params, setParams] = useSearchParams();
  const raw = params.get('filter') || 'all';
  const tile = TILES.find((t) => t.key === raw);
  const list = LISTS.find((l) => l.key === raw);
  const filter: string = tile || list ? raw : 'all';
  const period = params.get('period') || 'all';
  const sort = (params.get('sort') as SortMode) || 'importance';

  const all = useFeedAll(company?.company_id, period);
  const listFeed = useFeedAll(company?.company_id, period, list?.key ?? 'all');
  const feed = list ? listFeed : all;
  const active = tile ?? TILES[0];

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if ((k === 'filter' && v === 'all') || (k === 'period' && v === 'all') || (k === 'sort' && v === 'importance')) next.delete(k);
    else next.set(k, v);
    setParams(next, { replace: true });
  };

  const source = list ? (feed.data ?? []) : (all.data ?? []).filter(active.match);
  const items = sortInsights(source, sort);
  const empty = list ? list.empty : active.empty;

  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7" role="group" aria-label="Фильтр ленты">
        {TILES.map((t) => {
          const count = (all.data ?? []).filter(t.match).length;
          const on = !list && t.key === active.key;
          return (
            <button
              key={t.key}
              type="button"
              aria-pressed={on}
              onClick={() => set('filter', t.key)}
              className={cn(
                'pressable flex flex-col rounded-tile border-2 bg-white px-3.5 py-2.5 text-left shadow-card hover:-translate-y-0.5 hover:shadow-lift',
                on ? 'border-night' : 'border-transparent',
              )}
            >
              <CountUp
                value={all.isLoading ? undefined : count}
                className={cn('text-[22px] leading-tight font-bold tabular-nums', t.key === 'urgent' && count > 0 && 'text-accent')}
              />
              <span className="text-[13px] text-ink-2">{t.label}</span>
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Подборки">
          {LISTS.map((l) => (
            <Tag
              key={l.key}
              size={32}
              shape="rounded"
              view="filled"
              checked={list?.key === l.key}
              onClick={() => set('filter', list?.key === l.key ? 'all' : l.key)}
            >
              {l.label}
            </Tag>
          ))}
        </div>
        <div className="flex items-center gap-3 [&>div]:mb-0">
          <SortToggle value={sort} onChange={(v) => set('sort', v)} />
          <Select className="bg-white" value={period} onChange={(e) => set('period', e.target.value)} aria-label="Период">
            {PERIODS.map((p) => (
              <option key={p.key} value={p.key}>
                {p.label}
              </option>
            ))}
          </Select>
        </div>
      </div>



      {feed.isLoading && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}
      {feed.isError && <ErrorBox error={feed.error} onRetry={() => feed.refetch()} />}
      {!feed.isLoading && !feed.isError && items.length === 0 && <EmptyState title="Пусто" text={empty} />}

      {items.length > 0 && (
        <div
          key={`${company?.company_id}-${filter}-${period}-${sort}`}
          className={cn('card divide-y divide-line overflow-hidden transition-opacity duration-300', feed.isFetching && !feed.isLoading && 'opacity-60')}
        >
          {items.map((it, i) => (
            <InsightCardView key={it.insight_id} item={it} position={i + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
