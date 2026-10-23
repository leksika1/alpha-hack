import type { ReactNode } from 'react';
import { Status } from '@alfalab/core-components/status';
import { Button } from '@alfalab/core-components/button';
import { Skeleton } from '@alfalab/core-components/skeleton';
import { ApiError } from '../api/client';
import type { ImpactLevel, Polarity, StatementKind } from '../api/types';
import { IMPACT, IMPACT_MEANING, POLARITY, STATEMENT_KIND } from '../lib/format';

export function Card({ children, className = '', as: Tag = 'section' }: { children: ReactNode; className?: string; as?: 'section' | 'div' | 'article' }) {
  return <Tag className={`card ${className}`}>{children}</Tag>;
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

export function ImpactMeter({ level, score, compact = false }: { level: ImpactLevel; score?: number; compact?: boolean }) {
  const info = IMPACT[level] ?? IMPACT.low;
  const bars = { low: 1, medium: 2, high: 3, critical: 4 }[level] ?? 1;
  return (
    <span className={`impact impact--${info.tone}`} title={`${info.label}: ${IMPACT_MEANING[level] ?? ''}`}>
      <span className="impact__bars" aria-hidden="true">
        {[1, 2, 3, 4].map((i) => (
          <span key={i} className={i <= bars ? 'is-on' : ''} />
        ))}
      </span>
      {!compact && (
        <span className="impact__label">
          {info.label}
          {score !== undefined && <span className="impact__score"> · {score}/100</span>}
        </span>
      )}
    </span>
  );
}

// demo — утверждение опирается на синтетический демо-материал: его нельзя выдавать за аналитику Альфа-Банка.
export function KindBadge({ kind, demo = false }: { kind: StatementKind; demo?: boolean }) {
  const k = STATEMENT_KIND[kind];
  if (demo && kind === 'bank_analytics') {
    return (
      <span className="kind kind--demo" title="Синтетический демо-материал, не аналитика Альфа-Банка">
        Демо-материал (синтетика)
      </span>
    );
  }
  return (
    <span className={`kind kind--${kind}`} title={k?.hint}>
      {k?.label ?? kind}
    </span>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="error-box" role="alert">
      <div className="error-box__title">{e?.status === 403 ? 'Нет доступа к данным компании' : 'Не удалось загрузить данные'}</div>
      <div className="error-box__text">{e ? e.message : error instanceof Error ? error.message : 'Попробуйте ещё раз'}</div>
      {e?.traceId && <div className="error-box__trace">Код для поддержки: {e.traceId}</div>}
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
    <div className="empty">
      <div className="empty__title">{title}</div>
      {text && <div className="empty__text">{text}</div>}
      {action}
    </div>
  );
}

export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card card--pad">
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

export function SectionTitle({ children, aside, id }: { children: ReactNode; aside?: ReactNode; id?: string }) {
  return (
    <div className="section-title" id={id}>
      <h2>{children}</h2>
      {aside}
    </div>
  );
}

export function Disclaimers({ items }: { items: { code: string; text: string }[] }) {
  if (!items?.length) return null;
  return (
    <div className="disclaimers">
      {items.map((d) => (
        <p key={d.code}>{d.text}</p>
      ))}
    </div>
  );
}
