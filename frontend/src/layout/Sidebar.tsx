import type { ComponentType, SVGProps } from 'react';
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

export function Sidebar() {
  const toast = useToast();
  const { isAdmin } = useSession();
  const summary = useFeedSummary();
  const location = useLocation();
  const unread = summary.data?.unread ?? 0;
  const stub = (label: string) =>
    toast.show({ title: label, body: 'Раздел интернет-банка не входит в демо-прототип аналитики.', ttl: 3500 });

  return (
    <nav className="sidebar" aria-label="Разделы интернет-банка">
      <NavLink to="/" end className={({ isActive }) => `sidebar__item ${isActive ? 'is-active' : ''}`}>
        <CategoriesIcon />
        <span>Все сервисы</span>
      </NavLink>
      <div className="sidebar__divider" />
      {!isAdmin && (
        <NavLink
          to="/analytics"
          className={() => `sidebar__item sidebar__item--accent ${location.pathname.startsWith('/analytics') ? 'is-active' : ''}`}
        >
          <ChartLineAscMIcon />
          <span>Аналитика</span>
          {unread > 0 ? <span className="sidebar__counter">{unread > 99 ? '99+' : unread}</span> : <span className="sidebar__badge">AI</span>}
        </NavLink>
      )}
      {isAdmin && (
        <NavLink to="/admin" className={({ isActive }) => `sidebar__item sidebar__item--accent ${isActive ? 'is-active' : ''}`}>
          <DiagramMIcon />
          <span>Метрики сервиса</span>
        </NavLink>
      )}
      {STUB_ITEMS.map(({ label, icon: I, badge }) => (
        <button key={label} type="button" className="sidebar__item" onClick={() => stub(label)}>
          <I />
          <span>{label}</span>
          {badge && <span className="sidebar__badge sidebar__badge--blue">{badge}</span>}
        </button>
      ))}
      <div className="sidebar__divider" />
      <button type="button" className="sidebar__item" onClick={() => stub('Госзакупки')}>
        <BankMIcon />
        <span>Госзакупки</span>
      </button>
      <div className="sidebar__spacer" />
      <button type="button" className="sidebar__item sidebar__item--muted" onClick={() => stub('Настроить меню')}>
        <SettingsMIcon />
        <span>Настроить меню</span>
      </button>
    </nav>
  );
}
