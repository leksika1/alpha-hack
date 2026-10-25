// Оценка инсайта (CJM 08): полезно / бесполезно с причиной; показываем, что сервис сделал с отзывом.
import { useState, type ReactNode } from 'react';
import { Button } from '@alfalab/core-components/button';
import { ThumbUpMIcon } from '@alfalab/icons-glyph/ThumbUpMIcon';
import { ThumbDownMIcon } from '@alfalab/icons-glyph/ThumbDownMIcon';
import { useFeedback, useInsightState, type FeedbackResult } from '../../api/hooks';
import type { FeedbackIn } from '../../api/types';
import { Chip } from '../ui';
import { cn } from '../../lib/cn';

const REASONS: { key: NonNullable<FeedbackIn['reason']>; label: string }[] = [
  { key: 'irrelevant', label: 'Не относится к нам' },
  { key: 'obvious', label: 'Очевидно, ничего нового' },
  { key: 'wrong', label: 'Ошибка в выводах' },
  { key: 'outdated', label: 'Устарело' },
  { key: 'other', label: 'Другое' },
];

function VoteButton({ active = false, disabled, onClick, children }: { active?: boolean; disabled: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={cn(
        'inline-flex flex-1 items-center justify-center gap-1.5 rounded-xl border bg-white p-2 text-sm hover:border-night [&_svg]:size-[18px]',
        active ? 'border-night' : 'border-line',
      )}
    >
      {children}
    </button>
  );
}

export function FeedbackPanel({ insightId, hidden }: { insightId: string; hidden: boolean }) {
  const fb = useFeedback(insightId);
  const state = useInsightState(insightId);
  const [mode, setMode] = useState<'idle' | 'negative'>('idle');
  const [reason, setReason] = useState<FeedbackIn['reason']>(null);
  const [comment, setComment] = useState('');
  const [result, setResult] = useState<FeedbackResult | null>(null);

  const send = (useful: boolean) => fb.mutate({ useful, reason: useful ? null : reason ?? 'other', comment: comment || null }, { onSuccess: setResult });

  if (result) {
    return (
      <div className="flex flex-col gap-2.5">
        <b>Спасибо, отзыв учтён</b>
        <ul className="list-disc pl-[18px] text-sm text-ink-2">
          {(result.applied ?? []).map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        {(result.hidden || hidden) && (
          <Button view="secondary" size={32} onClick={() => state.mutate('unhide')} loading={state.isPending}>
            Вернуть инсайт в ленту
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2.5">
      <b>Инсайт был полезен?</b>
      <div className="flex gap-2">
        <VoteButton disabled={fb.isPending} onClick={() => send(true)}>
          <ThumbUpMIcon /> Полезно
        </VoteButton>
        <VoteButton active={mode === 'negative'} disabled={fb.isPending} onClick={() => setMode('negative')}>
          <ThumbDownMIcon /> Не полезно
        </VoteButton>
      </div>
      {mode === 'negative' && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap gap-1.5">
            {REASONS.map((r) => (
              <Chip key={r.key} active={reason === r.key} onClick={() => setReason(r.key)}>
                {r.label}
              </Chip>
            ))}
          </div>
          <textarea className="field-input" placeholder="Что было не так? (необязательно)" maxLength={1000} rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
          <Button view="primary" size={32} disabled={!reason} loading={fb.isPending} onClick={() => send(false)}>
            Отправить отзыв
          </Button>
          {reason === 'irrelevant' && <p className="text-[13px] text-ink-2">Нерелевантный инсайт будет скрыт, а похожие события опустятся ниже.</p>}
        </div>
      )}
      {fb.isError && <p className="text-[13px] text-neg">{fb.error.message}</p>}
    </div>
  );
}
