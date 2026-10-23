// Главная «Альфа-Бизнеса» (имитация): остатки, дела в работе и встроенный блок аналитики.
import type { ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { DocumentMIcon } from '@alfalab/icons-glyph/DocumentMIcon';
import { DocumentAddMIcon } from '@alfalab/icons-glyph/DocumentAddMIcon';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { ReceiptMIcon } from '@alfalab/icons-glyph/ReceiptMIcon';
import { CardMIcon } from '@alfalab/icons-glyph/CardMIcon';
import { AnalyticsWidget } from '../components/AnalyticsWidget';
import { useSession } from '../auth/session';
import { useToast } from '../components/Toasts';
import { useRequests } from '../api/hooks';
import { REQUEST_STATUS, ACTION_TYPES, formatDate } from '../lib/format';
import { Pill } from '../components/ui';
import { cn } from '../lib/cn';

const TASKS: [string, number, boolean][] = [
  ['Подписать и отправить', 1, true],
  ['В работе у банка', 7, true],
  ['Отклонённые', 4, true],
  ['Черновики', 1, false],
  ['Оплатить', 1, false],
  ['Принять предложение', 3, false],
];

function QuickButton({ onClick, muted = false, children }: { onClick: () => void; muted?: boolean; children: ReactNode }) {
  return (
    <button
      className={cn('inline-flex items-center gap-2.5 rounded-[14px] px-5 py-3 font-medium [&_svg]:size-5', muted ? 'bg-muted-2' : 'bg-white shadow-card')}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function HeroTile({ icon, title, sub, onClick }: { icon: ReactNode; title: string; sub: string; onClick: () => void }) {
  return (
    <button className="flex items-center gap-3 text-left text-white" onClick={onClick}>
      <span className="inline-flex size-10 items-center justify-center rounded-xl bg-white/10">{icon}</span>
      <span>
        {title}
        <small className="block text-xs text-white/55">{sub}</small>
      </span>
    </button>
  );
}

export function HomePage() {
  const { isAdmin, companies } = useSession();
  const toast = useToast();
  const requests = useRequests();
  if (isAdmin) return <Navigate to="/admin" replace />;

  const stub = (title: string) => toast.show({ title, body: 'Действие интернет-банка не входит в демо-прототип.', ttl: 3000 });

  return (
    <div>
      <div className="mt-1 mb-5 flex flex-wrap gap-2.5">
        <QuickButton onClick={() => stub('Скачать выписку')}>
          <DocumentMIcon /> Скачать выписку
        </QuickButton>
        <QuickButton onClick={() => stub('Импорт платежей')}>
          <DocumentAddMIcon /> Импорт платежей
        </QuickButton>
        <QuickButton onClick={() => stub('Создать платёж')}>
          <PlusMIcon /> Создать платёж
        </QuickButton>
        <QuickButton muted onClick={() => stub('Ещё')}>
          Ещё
        </QuickButton>
      </div>

      <section className="grid gap-3 rounded-3xl bg-[radial-gradient(700px_400px_at_70%_120%,rgba(239,49,36,0.55),transparent_60%),linear-gradient(120deg,#0b0b0d,#1b1416)] p-3 text-white md:grid-cols-[1.6fr_1fr]">
        <div className="rounded-[18px] bg-white/6 p-5">
          <div className="text-sm text-white/60">{companies.length > 1 ? `В ${companies.length} компаниях` : 'На счетах'}</div>
          <div className="my-1 text-[34px] font-bold tracking-[-0.01em]">2 524 550 000,00 RUB</div>
          <div className="text-white/60">↳ 10 800 570 344,95 RUB с овердрафтом</div>
          <div className="mt-3 mb-5 flex items-center gap-2.5 font-medium">
            <span>4 000,00 CNY</span>
            <span className="text-white/40">·</span>
            <span>40 000,00 KZT</span>
            <span className="rounded-lg bg-white/12 px-1.5 py-0.5 text-xs">+5</span>
          </div>
          <div className="flex gap-6 border-t border-white/10 pt-4">
            <HeroTile icon={<ReceiptMIcon />} title="Счета" sub="2 в рублях, 2 в валюте" onClick={() => stub('Счета')} />
            <HeroTile icon={<CardMIcon />} title="Бизнес-карты" sub="Реквизиты и операции" onClick={() => stub('Бизнес-карты')} />
          </div>
        </div>
        <div className="rounded-[18px] bg-white/6 p-5">
          <div className="mb-2 font-bold">Дела в работе</div>
          {TASKS.map(([label, n, red]) => (
            <button key={label} className="flex w-full justify-between py-[7px] text-white/92" onClick={() => stub(label)}>
              <span>{label}</span>
              <span className={cn('inline-flex h-5 min-w-5 items-center justify-center rounded-[10px] text-xs', red ? 'bg-accent' : 'bg-white/12')}>{n}</span>
            </button>
          ))}
        </div>
      </section>

      <AnalyticsWidget />

      <div className="mt-7 mb-4 flex gap-6 text-[22px] font-bold text-ink-3">
        <span className="text-ink">Сводка</span>
        <span>Остатки по компаниям</span>
        <span className="hidden md:inline">Мультибанк</span>
      </div>
      <div className="grid gap-4 md:grid-cols-[1.3fr_1fr]">
        <section className="card p-6">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-[17px]">Заявки из аналитики</h3>
            <span className="text-ink-2">{requests.data?.length ?? 0}</span>
          </div>
          {!requests.data?.length && <p className="text-ink-2">Здесь появятся обращения к менеджеру, звонки и заявки, оформленные из инсайтов.</p>}
          {requests.data?.slice(0, 3).map((r) => (
            <div key={r.request_id} className="flex items-center justify-between border-t border-line py-2.5">
              <div>
                <div className="font-semibold">{ACTION_TYPES[r.request_type] ?? r.request_type}</div>
                <div className="text-xs text-ink-2">
                  {formatDate(r.created_at)} · {r.external_ref}
                </div>
              </div>
              <Pill tone={REQUEST_STATUS[r.status]?.color ?? 'grey'}>{REQUEST_STATUS[r.status]?.label ?? r.status}</Pill>
            </div>
          ))}
        </section>
        <section className="card p-6">
          <h3 className="mb-3 text-[17px]">Конвертация</h3>
          <div className="grid grid-cols-[1.4fr_1fr_1fr] gap-3">
            <span className="text-ink-2">Валюта</span>
            <span className="text-ink-2">Продать</span>
            <span className="text-ink-2">Купить</span>
            <span>CNY/RUB</span>
            <span>9,97</span>
            <span>11,07</span>
            <span>EUR/RUB</span>
            <span>90,37</span>
            <span>91,47</span>
          </div>
        </section>
      </div>
    </div>
  );
}
