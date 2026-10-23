// Оценка инсайта (CJM 08): полезно / бесполезно с причиной; показываем, что сервис сделал с отзывом.
import { useState } from 'react';
import { Button } from '@alfalab/core-components/button';
import { ThumbUpMIcon } from '@alfalab/icons-glyph/ThumbUpMIcon';
import { ThumbDownMIcon } from '@alfalab/icons-glyph/ThumbDownMIcon';
import { useFeedback, useInsightState, type FeedbackResult } from '../../api/hooks';
import type { FeedbackIn } from '../../api/types';

const REASONS: { key: NonNullable<FeedbackIn['reason']>; label: string }[] = [
  { key: 'irrelevant', label: 'Не относится к нам' },
  { key: 'obvious', label: 'Очевидно, ничего нового' },
  { key: 'wrong', label: 'Ошибка в выводах' },
  { key: 'outdated', label: 'Устарело' },
  { key: 'other', label: 'Другое' },
];

export function FeedbackPanel({ insightId, hidden }: { insightId: string; hidden: boolean }) {
  const fb = useFeedback(insightId);
  const state = useInsightState(insightId);
  const [mode, setMode] = useState<'idle' | 'negative'>('idle');
  const [reason, setReason] = useState<FeedbackIn['reason']>(null);
  const [comment, setComment] = useState('');
  const [result, setResult] = useState<FeedbackResult | null>(null);

  const send = (useful: boolean) =>
    fb.mutate({ useful, reason: useful ? null : reason ?? 'other', comment: comment || null }, { onSuccess: setResult });

  if (result) {
    return (
      <div className="feedback feedback--done">
        <b>Спасибо, отзыв учтён</b>
        <ul>
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
    <div className="feedback">
      <b>Инсайт был полезен?</b>
      <div className="feedback__btns">
        <button className="feedback__btn" onClick={() => send(true)} disabled={fb.isPending}>
          <ThumbUpMIcon /> Полезно
        </button>
        <button className={`feedback__btn ${mode === 'negative' ? 'is-active' : ''}`} onClick={() => setMode('negative')} disabled={fb.isPending}>
          <ThumbDownMIcon /> Не полезно
        </button>
      </div>
      {mode === 'negative' && (
        <div className="feedback__more">
          <div className="feedback__reasons">
            {REASONS.map((r) => (
              <button key={r.key} type="button" className={`chip ${reason === r.key ? 'is-active' : ''}`} onClick={() => setReason(r.key)}>
                {r.label}
              </button>
            ))}
          </div>
          <textarea
            className="feedback__comment"
            placeholder="Что было не так? (необязательно)"
            maxLength={1000}
            rows={2}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
          />
          <Button view="primary" size={32} disabled={!reason} loading={fb.isPending} onClick={() => send(false)}>
            Отправить отзыв
          </Button>
          {reason === 'irrelevant' && <p className="muted small">Нерелевантный инсайт будет скрыт, а похожие события опустятся ниже.</p>}
        </div>
      )}
      {fb.isError && <p className="text-negative small">{fb.error.message}</p>}
    </div>
  );
}
