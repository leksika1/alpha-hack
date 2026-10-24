import type { ReactNode, SelectHTMLAttributes } from 'react';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';
import { Status } from '@alfalab/core-components/status';
import { Button } from '@alfalab/core-components/button';
import { Skeleton } from '@alfalab/core-components/skeleton';
import { ApiError } from '../api/client';
import type { ImpactLevel, Polarity, StatementKind } from '../api/types';
import { IMPACT, IMPACT_MEANING, POLARITY, STATEMENT_KIND } from '../lib/format';
import { cn } from '../lib/cn';

export function Card({ children, className, as: Tag = 'section', id }: { children: ReactNode; className?: string; as?: 'section' | 'div' | 'article'; id?: string }) {
  return (
    <Tag id={id} className={cn('card', className)}>
      {children}
    </Tag>
  );
}

export const POLARITY_TEXT: Record<Polarity, string> = {
  risk: 'text-neg',
  opportunity: 'text-pos',
  mixed: 'text-att',
  neutral: 'text-ink-2',
};

const PILL_TONES = {
  grey: 'bg-muted text-ink-2 font-medium',
  blue: 'bg-info-bg text-info',
  orange: 'bg-att-bg text-att',
  green: 'bg-pos-bg text-pos',
  red: 'bg-neg-bg text-neg',
} as const;

export type PillTone = keyof typeof PILL_TONES;

export function Pill({ tone = 'grey', children }: { tone?: PillTone; children: ReactNode }) {
  return <span className={cn('inline-flex items-center whitespace-nowrap rounded-[10px] px-2.5 py-0.5 text-xs font-semibold', PILL_TONES[tone])}>{children}</span>;
}

// Небольшие метки «Новое», «Обновлено».
export function Flag({ tone, children }: { tone: 'blue' | 'orange'; children: ReactNode }) {
  return (
    <span className={cn('rounded-md px-2 py-0.5 text-[11px] font-bold', tone === 'blue' ? 'bg-info-bg text-info' : 'bg-att-bg text-att')}>{children}</span>
  );
}

export function Chip({
  active = false,
  disabled,
  onClick,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'rounded-2xl border px-3 py-1.5 text-left text-[13px] disabled:cursor-default disabled:opacity-50',
        active ? 'border-night bg-night text-white' : 'border-line bg-white hover:border-ink-3',
      )}
    >
      {children}
    </button>
  );
}

export function PolarityStatus({ polarity }: { polarity: Polarity }) {
  const p = POLARITY[polarity] ?? POLARITY.neutral;
  return (
    <Status view="soft" color={p.color} size={20} uppercase={false}>
      {p.label}
    </Status>
  );
}

export function UrgentStatus() {
  return (
    <Status view="contrast" color="red" size={20} uppercase={false}>
      Срочно
    </Status>
  );
}

const BAR_ON: Record<string, string> = { low: 'bg-[#9aa0aa]', medium: 'bg-att-dot', high: 'bg-accent', critical: 'bg-accent' };
const BAR_HEIGHTS = ['h-1.5', 'h-[9px]', 'h-3', 'h-[15px]'];

export function ImpactMeter({ level, score, compact = false }: { level: ImpactLevel; score?: number; compact?: boolean }) {
  const info = IMPACT[level] ?? IMPACT.low;
  const bars = { low: 1, medium: 2, high: 3, critical: 4 }[level] ?? 1;
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap text-[13px]" title={`${info.label}: ${IMPACT_MEANING[level] ?? ''}`}>
      <span className="inline-flex items-end gap-0.5" aria-hidden="true">
        {BAR_HEIGHTS.map((h, i) => (
          <span key={h} className={cn('w-1 rounded-sm', h, i < bars ? BAR_ON[level] : 'bg-muted-2')} />
        ))}
      </span>
      {!compact && (
        <span className="font-semibold">
          {info.label}
          {score !== undefined && <span className="ml-1.5 font-normal text-ink-2">{score}/100</span>}
        </span>
      )}
    </span>
  );
}

const KIND_STYLES: Record<StatementKind, string> = {
  fact: 'bg-muted-2 text-[#2b2d33]',
  bank_analytics: 'bg-accent text-white',
  model_inference: 'bg-purple-bg text-purple',
  hypothesis: 'border border-dashed border-[#f1a64d] bg-att-bg text-[#b25e00]',
};

const KIND_BASE = 'inline-flex shrink-0 items-center whitespace-nowrap rounded-md px-2 py-0.5 align-[1px] text-[11px] font-bold tracking-[0.02em]';

// demo — утверждение опирается на синтетический демо-материал: его нельзя выдавать за аналитику Альфа-Банка.
// «(синтетика)» — всегда второй строкой, чтобы бейдж не растягивал колонку.
export function KindBadge({ kind, demo = false, className }: { kind: StatementKind; demo?: boolean; className?: string }) {
  const k = STATEMENT_KIND[kind];
  if (demo && kind === 'bank_analytics') {
    return (
      <span
        className={cn(KIND_BASE, 'flex-col border border-dashed border-neg bg-white text-center leading-tight text-neg', className)}
        title="Синтетический демо-материал, не аналитика Альфа-Банка"
      >
        <span>Демо-материал</span>
        <span>(синтетика)</span>
      </span>
    );
  }
  return (
    <span className={cn(KIND_BASE, KIND_STYLES[kind], className)} title={k?.hint}>
      {k?.label ?? kind}
    </span>
  );
}

export function Note({ tone = 'info', children }: { tone?: 'info' | 'warning'; children: ReactNode }) {
  return (
    <div className={cn('my-3 flex items-start gap-2 rounded-xl px-3.5 py-3 text-sm [&_svg]:shrink-0', tone === 'info' ? 'bg-info-bg' : 'bg-att-bg')}>{children}</div>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="flex flex-col items-start gap-2 rounded-card border border-accent/40 bg-white p-6" role="alert">
      <div className="font-bold">{e?.status === 403 ? 'Нет доступа к данным компании' : 'Не удалось загрузить данные'}</div>
      <div>{e ? e.message : error instanceof Error ? error.message : 'Попробуйте ещё раз'}</div>
      {e?.traceId && <div className="font-mono text-xs text-ink-2">Код для поддержки: {e.traceId}</div>}
      {onRetry && (
        <Button view="secondary" size={32} onClick={onRetry}>
          Повторить
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, text, action }: { title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="rounded-card bg-white px-6 py-10 text-center">
      <div className="mb-1.5 text-[17px] font-bold">{title}</div>
      {text && <div className="text-ink-2">{text}</div>}
      {action}
    </div>
  );
}

export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card mb-3 p-6">
      <Skeleton visible animate>
        <div style={{ height: 18, width: '40%', marginBottom: 12 }} />
      </Skeleton>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} visible animate>
          <div style={{ height: 14, width: `${90 - i * 12}%`, marginBottom: 10 }} />
        </Skeleton>
      ))}
    </div>
  );
}

export function H2({ children, className }: { children: ReactNode; className?: string }) {
  return <h2 className={cn('mt-2 mb-3 text-xl', className)}>{children}</h2>;
}

export function H3({ children, className }: { children: ReactNode; className?: string }) {
  return <h3 className={cn('mt-1 mb-3 text-[17px]', className)}>{children}</h3>;
}

export function Disclaimers({ items }: { items: { code: string; text: string }[] }) {
  if (!items?.length) return null;
  return (
    <div className="mx-1 my-5 text-xs text-ink-2">
      {items.map((d) => (
        <p key={d.code} className="mb-1">
          {d.text}
        </p>
      ))}
    </div>
  );
}

export function LinkButton({ onClick, children, className }: { onClick: () => void; children: ReactNode; className?: string }) {
  return (
    <button type="button" onClick={onClick} className={cn('inline-flex items-center gap-1 p-0 text-info hover:underline', className)}>
      {children}
    </button>
  );
}

const ACTION_TONE: Record<string, string> = {
  contact_manager: 'text-accent',
  callback: 'text-accent',
  application: 'text-accent',
  monitor: 'text-info',
};

// Подпись типа действия над его текстом: «Связаться с менеджером», «Отслеживать»…
export const actionLabelClass = (type: string) => cn('mb-0.5 text-xs font-semibold', ACTION_TONE[type] ?? 'text-ink-2');

// Нативный select без системной стрелки: своя иконка с отступом от края, как в полях Альфа-Бизнеса.
// field — поле в рамке (фон задаётся через className), inline — выбор внутри текста.
export function Select({
  variant = 'field',
  block = false,
  className,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { variant?: 'field' | 'inline'; block?: boolean }) {
  return (
    <span className={cn('relative inline-flex max-w-full items-center', block && 'flex w-full')}>
      <select
        {...props}
        className={cn(
          'w-full min-w-0 cursor-pointer appearance-none text-ink outline-none disabled:cursor-default disabled:opacity-60',
          variant === 'field' ? 'rounded-xl border border-line py-2.5 pr-10 pl-3.5' : 'bg-transparent pr-7 [field-sizing:content]',
          className,
        )}
      />
      <ChevronDownMIcon
        className={cn('pointer-events-none absolute top-1/2 size-5 -translate-y-1/2 text-ink-2', variant === 'field' ? 'right-3' : 'right-0')}
      />
    </span>
  );
}

export type SortMode = 'importance' | 'recent';

const IMPACT_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };

type Sortable = { published_at: string | null; impact_level: string; urgent: boolean; relevance_score: number };

// «По значимости» — по уровню влияния на компанию (те же столбики, что на карточке), внутри уровня срочные выше,
// дальше порядок бэкенда: релевантность для компании и роли с учётом свежести. «Сначала новые» — по дате публикации.
export function sortInsights<T extends Sortable>(items: T[], mode: SortMode): T[] {
  if (mode === 'importance') {
    return [...items].sort(
      (a, b) =>
        (IMPACT_RANK[b.impact_level] ?? 0) - (IMPACT_RANK[a.impact_level] ?? 0) ||
        Number(b.urgent) - Number(a.urgent) ||
        b.relevance_score - a.relevance_score,
    );
  }
  return [...items].sort((a, b) => new Date(b.published_at ?? 0).getTime() - new Date(a.published_at ?? 0).getTime());
}

export function SortToggle({ value, onChange }: { value: SortMode; onChange: (v: SortMode) => void }) {
  return (
    <Segmented
      label="Порядок"
      value={value}
      onChange={onChange}
      options={[
        { key: 'importance', label: 'По значимости' },
        { key: 'recent', label: 'Сначала новые' },
      ]}
    />
  );
}

// Сегментированный переключатель (день/неделя и т. п.).
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { key: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="mb-3 inline-flex rounded-xl bg-muted-2 p-[3px]" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={cn('rounded-[10px] px-4 py-1.5 font-semibold', value === o.key ? 'bg-white text-ink shadow-card' : 'text-ink-2')}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
