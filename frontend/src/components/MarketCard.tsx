// Ключевая ставка и официальные курсы Банка России на главной: значение, изменение к прошлой дате, источник.
import { Skeleton } from '@alfalab/core-components/skeleton';
import { useMarket } from '../api/hooks';
import type { MarketIndicator } from '../api/types';
import { formatDate } from '../lib/format';

const SHORT: Record<string, string> = {
  key_rate: 'Ключевая ставка',
  usd_rub: 'Доллар США',
  eur_rub: 'Евро',
  cny_rub: 'Юань',
};

const NUM = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function unitValue(it: MarketIndicator, value: number): string {
  return it.unit === '%' ? `${NUM.format(value)}%` : `${NUM.format(value)} ${it.unit}`;
}

function Change({ it }: { it: MarketIndicator }) {
  if (it.change == null) return null;
  if (it.change === 0) return <span className="text-ink-2">без изменений</span>;
  // Цвет нейтральный: рост курса или ставки одному бизнесу вредит, другому выгоден. Ставка — в п. п., курс — в рублях.
  const up = it.change > 0;
  const abs = NUM.format(Math.abs(it.change));
  return (
    <span className="text-ink-2 tabular-nums">
      <span className="font-medium">
        {up ? '▲' : '▼'} {it.unit === '%' ? `${abs} п. п.` : `${abs} ${it.unit}`}
      </span>
      {it.previous_date && ` к ${formatDate(it.previous_date)}`}
    </span>
  );
}

export function MarketCard() {
  const market = useMarket();
  const items = market.data?.items ?? [];
  if (market.isError || (!market.isLoading && items.length === 0)) return null;

  const source = items[0]?.source;
  const asOf = items.reduce<string | null>((max, it) => (!max || it.date > max ? it.date : max), null);

  return (
    <section className="card mt-4 p-6" aria-label="Ставка и курсы Банка России">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-[17px]">Ставка и курсы ЦБ</h3>
        {source && (
          <span className="text-[13px] text-ink-2">
            Источник:{' '}
            {source.url ? (
              <a href={source.url} target="_blank" rel="noreferrer">
                {source.title}
              </a>
            ) : (
              source.title
            )}
            {asOf && `, на ${formatDate(asOf)}`}
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        {market.isLoading &&
          [0, 1, 2, 3].map((i) => (
            <Skeleton key={i} visible animate>
              <div style={{ height: 64 }} />
            </Skeleton>
          ))}
        {items.map((it) => (
          <div key={it.indicator} className="flex flex-col gap-0.5" title={it.title}>
            <span className="text-[13px] text-ink-2">{SHORT[it.indicator] ?? it.title}</span>
            <span className="text-[22px] leading-tight font-bold tabular-nums">{unitValue(it, it.value)}</span>
            <span className="text-[13px]">
              <Change it={it} />
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
