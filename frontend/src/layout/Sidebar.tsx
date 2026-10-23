import type { ComponentType, ReactNode, SVGProps } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { CategoriesIcon } from './icons';
import { DocumentLinesMIcon } from '@alfalab/icons-glyph/DocumentLinesMIcon';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { MailMIcon } from '@alfalab/icons-glyph/MailMIcon';
import { ClockMIcon } from '@alfalab/icons-glyph/ClockMIcon';
import { DocumentMIcon } from '@alfalab/icons-glyph/DocumentMIcon';
import { ReceiptMIcon } from '@alfalab/icons-glyph/ReceiptMIcon';
import { UsersMIcon } from '@alfalab/icons-glyph/UsersMIcon';
import { DocumentInfoMIcon } from '@alfalab/icons-glyph/DocumentInfoMIcon';
import { CardMIcon } from '@alfalab/icons-glyph/CardMIcon';
import { BankMIcon } from '@alfalab/icons-glyph/BankMIcon';
import { SettingsMIcon } from '@alfalab/icons-glyph/SettingsMIcon';
import { ChartLineAscMIcon } from '@alfalab/icons-glyph/ChartLineAscMIcon';
import { DiagramMIcon } from '@alfalab/icons-glyph/DiagramMIcon';
import { useFeedSummary } from '../api/hooks';
import { useSession } from '../auth/session';
import { useToast } from '../components/Toasts';
import { cn } from '../lib/cn';

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

const STUB_ITEMS: { label: string; icon: Icon; badge?: string }[] = [
  { label: 'Заявки', icon: DocumentLinesMIcon, badge: 'НОВОЕ' },
  { label: 'Новый платёж', icon: PlusMIcon },
  { label: 'Лента операций', icon: MailMIcon },
  { label: 'Платежи в работе', icon: ClockMIcon },
  { label: 'Импорт реестров', icon: DocumentMIcon },
  { label: 'Выписка', icon: DocumentMIcon },
  { label: 'Счета', icon: ReceiptMIcon },
  { label: 'Контрагенты', icon: UsersMIcon },
  { label: 'Справки', icon: DocumentInfoMIcon },
  { label: 'Карты', icon: CardMIcon },
];

const ITEM =
  'flex w-full items-center gap-3 rounded-xl px-2.5 py-[9px] text-left text-sm text-ink hover:bg-muted-2 hover:no-underline [&_svg]:size-[22px] [&_svg]:shrink-0 [&_svg]:text-ink-2';
const ACTIVE = 'bg-white font-semibold shadow-card hover:bg-white';
const ACCENT_ICON = '[&_svg]:text-accent';

function Badge({ children, tone = 'red' }: { children: ReactNode; tone?: 'red' | 'blue' }) {
  return (
    <span className={cn('ml-auto rounded-lg px-[7px] py-0.5 text-[10px] font-bold tracking-[0.02em] text-white', tone === 'red' ? 'bg-accent' : 'bg-[#2288fa]')}>
      {children}
    </span>
  );
}

const Divider = () => <div className="mx-2.5 my-2 h-px bg-line" />;

export function Sidebar() {
  const toast = useToast();
  const { isAdmin } = useSession();
  const summary = useFeedSummary();
  const location = useLocation();
  const unread = summary.data?.unread ?? 0;
  const stub = (label: string) =>
    toast.show({ title: label, body: 'Раздел интернет-банка не входит в демо-прототип аналитики.', ttl: 3500 });

  return (
    <nav
      className="sticky top-[72px] hidden h-[calc(100vh-72px)] w-[216px] shrink-0 flex-col overflow-y-auto px-3 pt-2 pb-4 md:flex"
      aria-label="Разделы интернет-банка"
    >
      <NavLink to="/" end className={({ isActive }) => cn(ITEM, isActive && ACTIVE)}>
        <CategoriesIcon />
        <span>Все сервисы</span>
      </NavLink>
      <Divider />
      {!isAdmin && (
        <NavLink to="/analytics" className={() => cn(ITEM, ACCENT_ICON, location.pathname.startsWith('/analytics') && ACTIVE)}>
          <ChartLineAscMIcon />
          <span>Аналитика</span>
          {unread > 0 ? (
            <span className="ml-auto inline-flex h-5 min-w-[22px] items-center justify-center rounded-[10px] bg-accent px-1.5 text-[11px] font-bold text-white">
              {unread > 99 ? '99+' : unread}
            </span>
          ) : (
            <Badge>AI</Badge>
          )}
        </NavLink>
      )}
      {isAdmin && (
        <NavLink to="/admin" className={({ isActive }) => cn(ITEM, ACCENT_ICON, isActive && ACTIVE)}>
          <DiagramMIcon />
          <span>Метрики сервиса</span>
        </NavLink>
      )}
      {STUB_ITEMS.map(({ label, icon: I, badge }) => (
        <button key={label} type="button" className={ITEM} onClick={() => stub(label)}>
          <I />
          <span>{label}</span>
          {badge && <Badge tone="blue">{badge}</Badge>}
        </button>
      ))}
      <Divider />
      <button type="button" className={ITEM} onClick={() => stub('Госзакупки')}>
        <BankMIcon />
        <span>Госзакупки</span>
      </button>
      <div className="flex-1" />
      <button type="button" className={cn(ITEM, 'text-ink-2')} onClick={() => stub('Настроить меню')}>
        <SettingsMIcon />
        <span>Настроить меню</span>
      </button>
    </nav>
  );
}
