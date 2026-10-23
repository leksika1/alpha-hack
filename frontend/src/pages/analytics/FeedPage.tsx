// Лента инсайтов (CJM 03): ранжирование по значимости для компании и роли, фильтры, период.
import { useSearchParams } from 'react-router-dom';
import { Tag } from '@alfalab/core-components/tag';
import { useFeed } from '../../api/hooks';
import type { FeedFilter } from '../../api/types';
import { useSession } from '../../auth/session';
import { InsightCardView } from '../../components/InsightCardView';
import { CardSkeleton, EmptyState, ErrorBox, selectClass } from '../../components/ui';
import { ROLE_LABELS } from '../../lib/format';
import { cn } from '../../lib/cn';

const FILTERS: { key: FeedFilter; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'urgent', label: 'Срочные' },
  { key: 'risks', label: 'Риски' },
  { key: 'opportunities', label: 'Возможности' },
  { key: 'saved', label: 'Сохранённые' },
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
  saved: 'Сохраняйте инсайты значком закладки, чтобы вернуться к ним позже.',
  hidden: 'Скрытых инсайтов нет.',
};

export function FeedPage() {
  const { company } = useSession();
  const [params, setParams] = useSearchParams();
  const filter = (params.get('filter') as FeedFilter) || 'all';
  const period = params.get('period') || 'all';
  const feed = useFeed(company?.company_id, filter, period);

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if ((k === 'filter' && v === 'all') || (k === 'period' && v === 'all')) next.delete(k);
    else next.set(k, v);
    setParams(next, { replace: true });
  };

  const items = feed.data?.items ?? [];

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
        <select className={cn(selectClass, 'bg-white')} value={period} onChange={(e) => set('period', e.target.value)} aria-label="Период">
          {PERIODS.map((p) => (
            <option key={p.key} value={p.key}>
              {p.label}
            </option>
          ))}
        </select>
      </div>

      {company && (
        <p className="my-3 text-[13px] text-ink-2">
          Отсортировано по значимости для компании и роли «{ROLE_LABELS[company.role]}»: отрасль, контрагенты, страны ВЭД, продукты банка и свежесть
          события.
        </p>
      )}

      {feed.isLoading && [0, 1, 2].map((i) => <CardSkeleton key={i} />)}
      {feed.isError && <ErrorBox error={feed.error} onRetry={() => feed.refetch()} />}
      {!feed.isLoading && !feed.isError && items.length === 0 && <EmptyState title="Пусто" text={EMPTY[filter]} />}

      <div className={cn('flex flex-col gap-3 transition-opacity', feed.isFetching && !feed.isLoading && 'opacity-60')}>
        {items.map((it, i) => (
          <InsightCardView key={it.insight_id} item={it} position={i + 1} />
        ))}
      </div>
    </div>
  );
}
