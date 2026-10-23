// Действие после инсайта (CJM 07): менеджер, звонок или заявка → мок-CRM, с подтверждением результата.
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ModalDesktop } from '@alfalab/core-components/modal/desktop';
import { Button } from '@alfalab/core-components/button';
import { Textarea } from '@alfalab/core-components/textarea';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { useBankProducts, useCreateAction, type ActionResult } from '../../api/hooks';
import type { ActionIn } from '../../api/types';
import { REQUEST_STATUS } from '../../lib/format';
import { Chip, Note } from '../ui';
import { cn } from '../../lib/cn';

type CtaType = ActionIn['type'];

const TYPES: { key: CtaType; title: string; text: string }[] = [
  { key: 'contact_manager', title: 'Написать менеджеру', text: 'Персональный менеджер получит контекст инсайта и свяжется с вами.' },
  { key: 'callback', title: 'Заказать звонок', text: 'Перезвоним в удобное время, чтобы обсудить ситуацию.' },
  { key: 'application', title: 'Оставить заявку на продукт', text: 'Заявка уйдёт в банк вместе с обоснованием из инсайта.' },
];

const TIMES = ['Сегодня до 18:00', 'Завтра 10:00–13:00', 'Завтра 14:00–18:00'];

export interface ActionDraft {
  type: CtaType;
  productId: string | null;
  reason: string;
}

function Row({ label, value }: { label: string; value: string | undefined }) {
  return (
    <div className="flex justify-between border-b border-line py-2">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

export function ActionModal({ insightId, draft, onClose }: { insightId: string; draft: ActionDraft | null; onClose: () => void }) {
  const products = useBankProducts();
  const create = useCreateAction(insightId);
  const [type, setType] = useState<CtaType>('contact_manager');
  const [comment, setComment] = useState('');
  const [time, setTime] = useState(TIMES[1]);
  const [result, setResult] = useState<ActionResult | null>(null);

  useEffect(() => {
    if (draft) {
      setType(draft.type);
      setComment('');
      setResult(null);
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft]);

  const product = products.data?.find((p) => p.product_id === draft?.productId);

  const submit = () =>
    create.mutate(
      {
        type,
        product_id: type === 'application' || type === 'contact_manager' ? draft?.productId ?? null : null,
        comment: comment || null,
        preferred_time: type === 'callback' ? time : null,
      },
      { onSuccess: setResult },
    );

  return (
    <ModalDesktop open={!!draft} onClose={onClose} size={600}>
      <ModalDesktop.Header hasCloser title={result ? 'Обращение отправлено' : 'Следующий шаг'} />
      <ModalDesktop.Content>
        {result ? (
          <div className="flex flex-col gap-2.5">
            <span className="inline-flex size-14 items-center justify-center rounded-full bg-pos-bg text-pos">
              <CheckmarkMIcon />
            </span>
            <p className="text-[17px] font-semibold">{result.message ?? 'Обращение принято.'}</p>
            <Row label="Номер в CRM" value={result.external_ref} />
            <Row label="Статус" value={REQUEST_STATUS[result.status ?? 'new']?.label ?? result.status} />
            <p className="text-[13px] text-ink-2">
              Об изменении статуса придёт уведомление. Все обращения — во вкладке{' '}
              <Link to="/analytics/requests" onClick={onClose}>
                «Мои заявки»
              </Link>
              .
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-3.5">
            {draft?.reason && (
              <Note>
                <span>
                  <b>Связь с событием:</b> {draft.reason}
                </span>
              </Note>
            )}
            <div className="flex flex-col gap-2">
              {TYPES.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => setType(t.key)}
                  className={cn(
                    'flex flex-col gap-0.5 rounded-[14px] border bg-white px-3.5 py-3 text-left',
                    type === t.key ? 'border-night shadow-[inset_0_0_0_1px_#111]' : 'border-line',
                  )}
                >
                  <b>{t.title}</b>
                  <span className="text-[13px] text-ink-2">{t.text}</span>
                </button>
              ))}
            </div>
            {product && type !== 'callback' && (
              <div className="rounded-[14px] bg-muted px-3.5 py-3">
                <div className="text-xs text-ink-2">Продукт банка, связанный с событием</div>
                <b>{product.name}</b>
                <p className="text-[13px] text-ink-2">{product.description}</p>
              </div>
            )}
            {type === 'callback' && (
              <div className="flex flex-wrap gap-2" role="group" aria-label="Удобное время">
                {TIMES.map((t) => (
                  <Chip key={t} active={time === t} onClick={() => setTime(t)}>
                    {t}
                  </Chip>
                ))}
              </div>
            )}
            <Textarea label="Комментарий для банка (необязательно)" block minRows={3} maxLength={1000} value={comment} onChange={(_, { value }) => setComment(value)} />
            <p className="text-[13px] text-ink-2">Мы передадим менеджеру название инсайта и компанию. Финансовые данные компании в заявку не копируются.</p>
            {create.isError && <Note tone="warning">{create.error.message}</Note>}
          </div>
        )}
      </ModalDesktop.Content>
      <ModalDesktop.Footer>
        <div className="flex gap-2">
          {result ? (
            <Button view="primary" size={48} onClick={onClose}>
              Готово
            </Button>
          ) : (
            <>
              <Button view="accent" size={48} loading={create.isPending} onClick={submit}>
                Отправить
              </Button>
              <Button view="secondary" size={48} onClick={onClose}>
                Отмена
              </Button>
            </>
          )}
        </div>
      </ModalDesktop.Footer>
    </ModalDesktop>
  );
}
