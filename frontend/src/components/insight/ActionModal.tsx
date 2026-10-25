// Действие после инсайта (CJM 07): менеджер, звонок или заявка → мок-CRM, с подтверждением результата.
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ModalDesktop } from '@alfalab/core-components/modal/desktop';
import { Button } from '@alfalab/core-components/button';
import { Textarea } from '@alfalab/core-components/textarea';
import { StatusBadge } from '@alfalab/core-components/status-badge';
import { useBankProducts, useCreateAction, type ActionResult } from '../../api/hooks';
import type { ActionIn } from '../../api/types';
import { REQUEST_STATUS } from '../../lib/format';
import { Chip, Note } from '../ui';
import { cn } from '../../lib/cn';

type CtaType = ActionIn['type'];

// Подписи формы зависят от выбранного действия: поле комментария, что уйдёт в банк и кнопка отправки.
const TYPES: { key: CtaType; title: string; text: string; productLabel: string; comment: string; note: string; submit: string }[] = [
  {
    key: 'contact_manager',
    title: 'Написать менеджеру',
    text: 'Персональный менеджер получит контекст инсайта и свяжется с вами.',
    productLabel: 'Менеджер расскажет о продукте',
    comment: 'Сообщение менеджеру (необязательно)',
    note: 'Менеджер увидит название инсайта, компанию и ваше сообщение и ответит в рабочее время. Финансовые данные компании в обращение не копируются.',
    submit: 'Написать менеджеру',
  },
  {
    key: 'callback',
    title: 'Заказать звонок',
    text: 'Перезвоним в удобное время, чтобы обсудить ситуацию.',
    productLabel: '',
    comment: 'Что обсудить на звонке (необязательно)',
    note: 'Менеджер позвонит в выбранное время и заранее увидит, о каком событии речь. Финансовые данные компании в заявку не копируются.',
    submit: 'Заказать звонок',
  },
  {
    key: 'application',
    title: 'Оставить заявку на продукт',
    text: 'Заявка уйдёт в банк вместе с обоснованием из инсайта.',
    productLabel: 'Продукт в заявке',
    comment: 'Комментарий к заявке (необязательно)',
    note: 'К заявке приложим название инсайта и связь с событием, статус появится во вкладке «Мои заявки». Финансовые данные компании в заявку не копируются.',
    submit: 'Отправить заявку',
  },
];

const TIMES = ['Сегодня до 18:00', 'Завтра 10:00–13:00', 'Завтра 14:00–18:00'];

export interface ActionDraft {
  type: CtaType;
  productId: string | null;
  reason: string;
}

// Плавно раскрывающийся блок: высота анимируется через grid-template-rows 0fr → 1fr.
function Reveal({ open, children }: { open: boolean; children: ReactNode }) {
  return (
    <div
      className={cn(
        'grid transition-[grid-template-rows,opacity,margin] duration-300 ease-alfa',
        open ? 'grid-rows-[1fr] opacity-100' : '-mt-3.5 grid-rows-[0fr] opacity-0',
      )}
      aria-hidden={!open}
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  );
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
  const cfg = TYPES.find((t) => t.key === type) ?? TYPES[0];

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
          <div className="flex flex-col gap-2.5 motion-safe:animate-fade-up">
            <StatusBadge view="positive-checkmark" size={40} className="motion-safe:animate-pop-in" />
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
                  aria-pressed={type === t.key}
                  className={cn(
                    'pressable flex flex-col gap-0.5 rounded-[14px] border bg-white px-3.5 py-3 text-left',
                    type === t.key ? 'border-night shadow-[inset_0_0_0_1px_#111]' : 'border-line hover:border-ink-3 hover:bg-muted/40',
                  )}
                >
                  <b>{t.title}</b>
                  <span className="text-[13px] text-ink-2">{t.text}</span>
                </button>
              ))}
            </div>
            {product && (
              <Reveal open={type !== 'callback'}>
                <div className="rounded-[14px] bg-muted px-3.5 py-3">
                  <div key={type} className="text-xs text-ink-2 motion-safe:animate-fade-in">
                    {cfg.productLabel || TYPES[0].productLabel}
                  </div>
                  <b>{product.name}</b>
                  <p className="text-[13px] text-ink-2">{product.description}</p>
                </div>
              </Reveal>
            )}
            <Reveal open={type === 'callback'}>
              <div className="flex flex-wrap gap-2" role="group" aria-label="Удобное время">
                {TIMES.map((t) => (
                  <Chip key={t} active={time === t} onClick={() => setTime(t)}>
                    {t}
                  </Chip>
                ))}
              </div>
            </Reveal>
            <Textarea label={cfg.comment} block minRows={3} maxLength={1000} value={comment} onChange={(_, { value }) => setComment(value)} />
            <p key={type} className="text-[13px] text-ink-2 motion-safe:animate-fade-in">
              {cfg.note}
            </p>
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
                <span key={type} className="inline-block motion-safe:animate-fade-in">
                  {cfg.submit}
                </span>
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
