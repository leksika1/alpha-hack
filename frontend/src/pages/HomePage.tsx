// Главная «Альфа-Бизнеса» (имитация): тёмная карточка остатков, блок аналитики, сводка.
import { useState, type ReactNode } from 'react';
import { Navigate } from 'react-router-dom';
import { DocumentMIcon } from '@alfalab/icons-glyph/DocumentMIcon';
import { DocumentAddMIcon } from '@alfalab/icons-glyph/DocumentAddMIcon';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { ReceiptMIcon } from '@alfalab/icons-glyph/ReceiptMIcon';
import { CardMIcon } from '@alfalab/icons-glyph/CardMIcon';
import { PlusCircleMIcon } from '@alfalab/icons-glyph/PlusCircleMIcon';
import { CrossSIcon } from '@alfalab/icons-glyph/CrossSIcon';
import { ChevronRightMIcon } from '@alfalab/icons-glyph/ChevronRightMIcon';
import { EyeMIcon } from '@alfalab/icons-glyph/EyeMIcon';
import { EyeOffMIcon } from '@alfalab/icons-glyph/EyeOffMIcon';
import { GearMIcon } from '@alfalab/icons-glyph/GearMIcon';
import { AnalyticsWidget } from '../components/AnalyticsWidget';
import { MarketCard } from '../components/MarketCard';
import { useSession } from '../auth/session';
import { useToast } from '../components/Toasts';
import { useRequests } from '../api/hooks';
import { REQUEST_STATUS, ACTION_TYPES, formatDate } from '../lib/format';
import { Pill } from '../components/ui';
import { cn } from '../lib/cn';

// Третий элемент — красный счётчик (как в интернет-банке), нулевые строки приглушены.
const TASKS: [string, number, boolean][] = [
  ['Подписать и отправить', 1, true],
  ['В работе у банка', 7, true],
  ['Отклонённые', 4, true],
  ['Черновики', 1, false],
  ['Оплатить', 1, false],
  ['Доработать', 0, false],
  ['Принять предложение', 3, false],
];

// Заявки интернет-банка вне аналитики — статичные, для полноты витрины.
const BANK_REQUESTS = [
  { title: 'Лизинг', amount: '75 000 ₽', date: '09.04.2026', company: 'ООО «Центрсвет Бутово»', status: 'На подпись', step: '2 из 5' },
  { title: 'Факторинг', amount: '1 200 000 ₽', date: '02.04.2026', company: 'ООО «Город Нагатино»', status: 'На подпись', step: '1 из 4' },
];

const HIDDEN = '••• ••• •••';

function QuickButton({ onClick, muted = false, children }: { onClick: () => void; muted?: boolean; children: ReactNode }) {
  return (
    <button
      className={cn(
        'pressable inline-flex items-center gap-2.5 rounded-[14px] px-5 py-3 font-medium [&_svg]:size-5',
        muted ? 'bg-muted-2 hover:bg-[#dfe0e3]' : 'bg-white shadow-card hover:shadow-lift',
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function HeroTile({ icon, title, sub, onClick }: { icon: ReactNode; title: ReactNode; sub?: string; onClick: () => void }) {
  return (
    <button className="pressable group flex items-center gap-3 text-left text-white" onClick={onClick}>
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl bg-white/10 transition-colors group-hover:bg-white/20">{icon}</span>
      <span className="leading-snug">
        {title}
        {sub && <small className="block text-xs text-white/55">{sub}</small>}
      </span>
    </button>
  );
}

// Иллюстрация промо «Умная карта»: металлическая карта на красной подложке.
function SmartCardArt() {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 h-[58%] transition-transform duration-500 ease-(--ease-alfa) group-hover:-translate-y-1" aria-hidden>
      <div className="absolute right-[-10%] bottom-[-8%] h-[34%] w-[82%] -rotate-3 rounded-2xl bg-linear-120 from-[#ff4a3d] to-[#9e0f08]" />
      <div className="absolute right-[-22%] bottom-[16%] h-[56%] w-[92%] -rotate-[8deg] skew-y-[-3deg] rounded-[22px] bg-linear-135 from-[#f4f5f8] via-[#a9abb3] to-[#e1e2e6] shadow-[0_18px_40px_rgba(0,0,0,0.55)]">
        <div className="absolute top-[18%] left-[8%] h-[34%] w-[24%] [clip-path:polygon(25%_0,75%_0,100%_50%,75%_100%,25%_100%,0_50%)] bg-linear-160 from-white to-[#b8bac2]" />
        <div className="absolute right-[14%] bottom-[16%] font-mono text-[13px] tracking-[0.12em] text-[#5dff7a] drop-shadow-[0_0_6px_rgba(93,255,122,0.7)]">
          1010 0 1010 0
        </div>
      </div>
    </div>
  );
}

// Полукруглый индикатор риска для одной компании.
function RiskGauge() {
  return (
    <div className="relative mx-auto mt-2 w-full max-w-[260px]">
      <svg viewBox="0 0 200 110" className="w-full">
        <path d="M 14 100 A 86 86 0 0 1 186 100" fill="none" stroke="#e7e8ea" strokeWidth="8" strokeLinecap="round" />
        <path d="M 14 100 A 86 86 0 0 1 40 39" fill="none" stroke="#0cc44d" strokeWidth="8" strokeLinecap="round" />
        <circle cx="40" cy="39" r="6" fill="#fff" stroke="#0cc44d" strokeWidth="3" />
      </svg>
      <div className="absolute inset-x-0 bottom-1 text-center">
        <div className="font-bold">Низкий риск</div>
        <div className="text-xs text-ink-2">Действия компании не вызывают вопросов</div>
      </div>
    </div>
  );
}

function StoryTile({ title, tone, onClick }: { title: string; tone: 'dark' | 'red'; onClick: () => void }) {
  return (
    <button
      className={cn(
        'pressable relative flex h-[150px] w-[112px] shrink-0 flex-col justify-start self-start overflow-hidden rounded-2xl border-[3px] border-[#a7231b] p-2.5 text-left text-[13px] leading-tight font-bold text-white hover:-translate-y-0.5',
        tone === 'dark'
          ? 'bg-[radial-gradient(70%_50%_at_50%_95%,rgba(239,49,36,0.55),transparent),linear-gradient(#202024,#0c0c0e)]'
          : 'bg-[radial-gradient(80%_60%_at_80%_100%,rgba(0,0,0,0.6),transparent),linear-gradient(160deg,#ef3124,#8f140c)]',
      )}
      onClick={onClick}
    >
      {title}
    </button>
  );
}

export function HomePage() {
  const { isAdmin, companies } = useSession();
  const toast = useToast();
  const requests = useRequests();
  const [promoOpen, setPromoOpen] = useState(true);
  const [hidden, setHidden] = useState(false);
  if (isAdmin) return <Navigate to="/admin" replace />;

  const multi = companies.length > 1;
  const stub = (title: string) => toast.show({ title, body: 'Действие интернет-банка не входит в демо-прототип.', ttl: 3000 });
  const money = (value: string) => (hidden ? HIDDEN : value);
  const analyticsRequests = requests.data ?? [];
  const tabs = ['Сводка', multi ? 'Остатки по компаниям' : 'Счета на главной', 'Мультибанк', 'Металлы'];

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

      <section className="grid gap-3 rounded-[28px] bg-black bg-[radial-gradient(40%_70%_at_62%_110%,rgba(239,49,36,0.5),transparent_70%)] p-3 text-white md:grid-cols-2 lg:grid-cols-[1.65fr_0.85fr_0.85fr]">
        <div className="flex flex-col rounded-[20px] bg-linear-100 from-[#1c1c1f] from-55% to-[#2a1513] p-5 md:col-span-2 lg:col-span-1">
          {multi && <div className="text-sm text-white/60">В {companies.length} компаниях</div>}
          <div className="my-1 text-[34px] leading-tight font-bold tracking-[-0.01em] tabular-nums">{money('2 524 550 000,00 RUB')}</div>
          <div className="text-white/55">↳ {money('10 800 570 344,95 RUB')} с овердрафтом</div>
          <div className="mt-3 flex items-center gap-4 font-medium tabular-nums">
            <span>{money('4 000,00 CNY')}</span>
            <span>{money('40 000,00 KZT')}</span>
            <span className="rounded-lg bg-white/12 px-1.5 py-0.5 text-xs">+5</span>
          </div>

          <div className="mt-auto pt-8">
            {promoOpen && (
              <div className="flex items-center gap-2 border-b border-white/10 pb-4 text-white/60 motion-safe:animate-fade-in">
                <PlusCircleMIcon className="size-5 shrink-0 text-pos-dot" />
                <button className="flex flex-wrap items-center gap-x-2 text-left hover:text-white" onClick={() => stub('Переводы себе')}>
                  Переводите себе бесплатно
                  <b className="font-bold text-white">5 000 000 ₽</b>
                  <span className="rounded-md bg-pos-dot px-1.5 py-px text-[11px] font-bold text-black uppercase">одобрено</span>
                </button>
                <button className="ml-auto rounded-lg p-1 text-white/60 transition-colors hover:bg-white/10 hover:text-white" aria-label="Скрыть" onClick={() => setPromoOpen(false)}>
                  <CrossSIcon />
                </button>
              </div>
            )}
            <div className="grid grid-cols-2 gap-6 pt-4">
              <HeroTile icon={<ReceiptMIcon />} title="Счета" sub={multi ? '2 в рублях, 2 в валюте' : '8 в рублях, 2 в валюте'} onClick={() => stub('Счета')} />
              {multi ? (
                <HeroTile icon={<CardMIcon />} title="Бизнес-карты" sub="Реквизиты и операции" onClick={() => stub('Бизнес-карты')} />
              ) : (
                <HeroTile icon={<PlusMIcon />} title={<>Заказать<br />бизнес-карту</>} onClick={() => stub('Заказать бизнес-карту')} />
              )}
            </div>
          </div>
        </div>

        <div className="rounded-[20px] bg-linear-160 from-[#1f1f22] from-50% to-[#33140f] px-2 py-5">
          <div className="mb-2 px-3 font-bold">Дела в работе</div>
          {TASKS.map(([label, n, red]) => (
            <button
              key={label}
              disabled={n === 0}
              className="flex w-full items-center justify-between rounded-xl px-3 py-[7px] text-white/92 transition-colors duration-200 hover:bg-white/6 hover:text-white disabled:text-white/35 disabled:hover:bg-transparent"
              onClick={() => stub(label)}
            >
              <span>{label}</span>
              <span className={cn('inline-flex h-5 min-w-5 items-center justify-center rounded-[10px] px-1.5 text-xs font-medium', red ? 'bg-accent' : 'bg-white/12', n === 0 && 'text-white/40')}>
                {n}
              </span>
            </button>
          ))}
        </div>

        <button
          className="pressable group relative flex min-h-[300px] flex-col items-start justify-start overflow-hidden rounded-[20px] bg-linear-160 from-[#2a2a2e] from-30% to-[#1a1a1d] p-5 text-left"
          onClick={() => stub('Умная карта для всех')}
        >
          <div className="relative z-10 font-bold">Умная карта для всех</div>
          <div className="relative z-10 mt-1.5 max-w-[200px] text-[13px] text-white/70">Кэшбэк, бонусы и удобные платежи</div>
          <SmartCardArt />
          {multi && (
            <span className="absolute bottom-4 left-4 z-10 rounded-lg bg-white/15 px-2 py-1 text-xs backdrop-blur-md">Для {Math.min(3, companies.length)} компаний</span>
          )}
        </button>

        <div className="flex gap-8 px-5 pt-1 pb-2 md:col-span-2 lg:col-span-1">
          <button className="transition-colors hover:text-white/70" onClick={() => stub('Реквизиты')}>
            Реквизиты
          </button>
          <button className="transition-colors hover:text-white/70" onClick={() => stub('Тариф')}>
            Тариф
          </button>
        </div>
      </section>

      <AnalyticsWidget />
      <MarketCard />

      <div className="mt-8 mb-4 flex flex-wrap items-center gap-x-7 gap-y-2">
        {tabs.map((t, i) => (
          <button
            key={t}
            className={cn('text-[22px] font-bold tracking-[-0.01em] transition-colors', i === 0 ? 'text-ink' : 'text-ink-3 hover:text-ink-2')}
            aria-current={i === 0 ? 'page' : undefined}
            onClick={() => i > 0 && stub(t)}
          >
            {t}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-4">
          <div className="flex rounded-xl bg-muted-2 p-1" role="group" aria-label="Показ остатков">
            <button
              className={cn('rounded-lg p-1.5 transition-colors [&_svg]:size-5', !hidden ? 'bg-white shadow-card' : 'text-ink-3 hover:text-ink-2')}
              aria-pressed={!hidden}
              aria-label="Показать остатки"
              onClick={() => setHidden(false)}
            >
              <EyeMIcon />
            </button>
            <button
              className={cn('rounded-lg p-1.5 transition-colors [&_svg]:size-5', hidden ? 'bg-white shadow-card' : 'text-ink-3 hover:text-ink-2')}
              aria-pressed={hidden}
              aria-label="Скрыть остатки"
              onClick={() => setHidden(true)}
            >
              <EyeOffMIcon />
            </button>
          </div>
          <button className="rounded-lg p-1 transition-colors hover:bg-muted-2" aria-label="Настроить главную" onClick={() => stub('Настройки главной')}>
            <GearMIcon />
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr_auto]">
        <section className="card p-6">
          <button className="mb-3 flex w-full items-center gap-2 text-left" onClick={() => stub('Активные заявки')}>
            <h3 className="text-[17px]">Активные заявки</h3>
            <span className="rounded-md bg-muted-2 px-1.5 text-xs font-medium text-ink-2">{analyticsRequests.length + BANK_REQUESTS.length}</span>
            <ChevronRightMIcon className="ml-auto" />
          </button>
          {analyticsRequests.slice(0, 3).map((r) => (
            <div key={r.request_id} className="flex items-center justify-between gap-3 border-b border-line py-2.5">
              <div className="min-w-0">
                <div className="font-medium">{ACTION_TYPES[r.request_type] ?? r.request_type}</div>
                <div className="text-xs text-ink-2">
                  {formatDate(r.created_at)}, из Альфа-Аналитики, {r.external_ref}
                </div>
              </div>
              <Pill tone={REQUEST_STATUS[r.status]?.color ?? 'grey'}>{REQUEST_STATUS[r.status]?.label ?? r.status}</Pill>
            </div>
          ))}
          {BANK_REQUESTS.slice(0, Math.max(1, 4 - Math.min(3, analyticsRequests.length))).map((r) => (
            <div key={r.title} className="border-b border-line py-2.5 last:border-b-0">
              <div className="font-medium">{r.title}</div>
              <div className="flex items-center justify-between gap-3">
                <span>{money(r.amount)}</span>
                <Pill tone="blue">{r.status}</Pill>
              </div>
              <div className="flex items-center justify-between gap-3 text-xs text-ink-2">
                <span>{multi ? `${r.date}, ${r.company}` : r.date}</span>
                <span>{r.step}</span>
              </div>
            </div>
          ))}
        </section>

        {multi ? (
          <section className="card p-6">
            <button className="mb-4 flex w-full items-center text-left" onClick={() => stub('Конвертация')}>
              <h3 className="text-[17px]">Конвертация</h3>
              <ChevronRightMIcon className="ml-auto" />
            </button>
            <div className="grid grid-cols-[1.4fr_1fr_1fr] gap-x-3 gap-y-3.5 tabular-nums">
              <span className="text-xs text-ink-2">Валюта</span>
              <span className="text-right text-xs text-ink-2">Продать</span>
              <span className="text-right text-xs text-ink-2">Купить</span>
              <span>CNY/RUB</span>
              <span className="text-right">9,97</span>
              <span className="text-right">11,07</span>
              <span>EUR/RUB</span>
              <span className="text-right">90,37</span>
              <span className="text-right">91,47</span>
              <span>USD/RUB</span>
              <span className="text-right">80,12</span>
              <span className="text-right">81,04</span>
            </div>
          </section>
        ) : (
          <section className="card p-6">
            <button className="flex w-full items-center text-left" onClick={() => stub('Индикатор риска')}>
              <h3 className="text-[17px]">Индикатор риска</h3>
              <ChevronRightMIcon className="ml-auto" />
            </button>
            <RiskGauge />
          </section>
        )}

        <div className="flex gap-3">
          <StoryTile title="Как получить деньги на бизнес" tone="dark" onClick={() => stub('Как получить деньги на бизнес')} />
          <StoryTile title="Альфа-Саммит" tone="red" onClick={() => stub('Альфа-Саммит')} />
        </div>
      </div>
    </div>
  );
}
