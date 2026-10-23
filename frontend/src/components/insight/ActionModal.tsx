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
          <div className="action-done">
            <span className="action-done__icon">
              <CheckmarkMIcon />
            </span>
            <p className="action-done__msg">{result.message ?? 'Обращение принято.'}</p>
            <div className="action-done__row">
              <span>Номер в CRM</span>
              <b>{result.external_ref}</b>
            </div>
            <div className="action-done__row">
              <span>Статус</span>
              <b>{REQUEST_STATUS[result.status ?? 'new']?.label ?? result.status}</b>
            </div>
            <p className="muted small">
              Об изменении статуса придёт уведомление. Все обращения — во вкладке <Link to="/analytics/requests" onClick={onClose}>«Мои заявки»</Link>.
            </p>
          </div>
        ) : (
          <div className="action-form">
            {draft?.reason && (
              <div className="note note--info">
                <span>
                  <b>Связь с событием:</b> {draft.reason}
                </span>
              </div>
            )}
            <div className="action-types">
              {TYPES.map((t) => (
                <button key={t.key} type="button" className={`action-type ${type === t.key ? 'is-active' : ''}`} onClick={() => setType(t.key)}>
                  <b>{t.title}</b>
                  <span>{t.text}</span>
                </button>
              ))}
            </div>
            {product && type !== 'callback' && (
              <div className="product-box">
                <div className="product-box__label">Продукт банка, связанный с событием</div>
                <b>{product.name}</b>
                <p className="muted small">{product.description}</p>
              </div>
            )}
            {type === 'callback' && (
              <div className="time-picker" role="group" aria-label="Удобное время">
                {TIMES.map((t) => (
                  <button key={t} type="button" className={`chip ${time === t ? 'is-active' : ''}`} onClick={() => setTime(t)}>
                    {t}
                  </button>
                ))}
              </div>
            )}
            <Textarea
              label="Комментарий для банка (необязательно)"
              block
              minRows={3}
              maxLength={1000}
              value={comment}
              onChange={(_, { value }) => setComment(value)}
            />
            <p className="muted small">Мы передадим менеджеру название инсайта и компанию. Финансовые данные компании в заявку не копируются.</p>
            {create.isError && <div className="note note--warning">{create.error.message}</div>}
          </div>
        )}
      </ModalDesktop.Content>
      <ModalDesktop.Footer>
        <div className="modal-actions">
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
