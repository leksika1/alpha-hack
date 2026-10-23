// Лента инсайтов (CJM 03): ранжирование по значимости для компании и роли, фильтры, период.
import { useSearchParams } from 'react-router-dom';
import { Tag } from '@alfalab/core-components/tag';
import { useFeedAll } from '../../api/hooks';
import type { FeedFilter } from '../../api/types';
import { useSession } from '../../auth/session';
import { InsightCardView } from '../../components/InsightCardView';
import { CardSkeleton, EmptyState, ErrorBox, Select, SortToggle, sortInsights, type SortMode } from '../../components/ui';
import { ROLE_LABELS } from '../../lib/format';
import { cn } from '../../lib/cn';

const FILTERS: { key: FeedFilter; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'urgent', label: 'Срочные' },
  { key: 'risks', label: 'Риски' },
  { key: 'opportunities', label: 'Возможности' },
  { key: 'saved', label: 'Отслеживаемые' },
  { key: 'hidden', label: 'Скрытые' },
];

const PERIODS = [
  { key: 'all', label: 'За всё время' },
  { key: 'day', label: 'За сутки' },
  { key: 'week', label: 'За неделю' },
  { key: 'month', label: 'За месяц' },
];

const EMPTY: Record<FeedFilter, string> = {
  all: 'Значимых событий для компании пока нет. Новости проверяются каждые 15 минут.',
  urgent: 'Срочных событий нет — можно выдохнуть.',
  risks: 'Рисков за выбранный период не найдено.',
  opportunities: 'Возможностей за выбранный период не найдено.',
  saved: 'Отмечайте инсайты значком закладки — они появятся здесь, а об обновлениях событий придут уведомления.',
  hidden: 'Скрытых инсайтов нет.',
};

export function FeedPage() {
  const { company } = useSession();
  const [params, setParams] = useSearchParams();
  const filter = (params.get('filter') as FeedFilter) || 'all';
  const period = params.get('period') || 'all';
  const sort = (params.get('sort') as SortMode) || 'importance';
  const feed = useFeedAll(company?.company_id, period, filter);

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if ((k === 'filter' && v === 'all') || (k === 'period' && v === 'all') || (k === 'sort' && v === 'importance')) next.delete(k);
    else next.set(k, v);
    setParams(next, { replace: true });
  };

  const items = sortInsights(feed.data ?? [], sort);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Фильтр ленты">
          {FILTERS.map((f) => (
            <Tag key={f.key} size={32} shape="rounded" view="filled" checked={filter === f.key} onClick={() => set('filter', f.key)}>
              {f.label}
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

      {company && (
        <p className="my-3 text-[13px] text-ink-2">
          {sort === 'importance'
            ? `Сначала самое значимое для компании и роли «${ROLE_LABELS[company.role]}»: отрасль, контрагенты, страны ВЭД, продукты банка и свежесть события.`
            : 'Сначала самые свежие события.'}
        </p>
      )}

      {feed.isLoading && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}
      {feed.isError && <ErrorBox error={feed.error} onRetry={() => feed.refetch()} />}
      {!feed.isLoading && !feed.isError && items.length === 0 && <EmptyState title="Пусто" text={EMPTY[filter]} />}

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
