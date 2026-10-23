import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { MailMIcon } from '@alfalab/icons-glyph/MailMIcon';
import { BellMIcon } from '@alfalab/icons-glyph/BellMIcon';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';
import { CheckmarkMIcon } from '@alfalab/icons-glyph/CheckmarkMIcon';
import { LogOutMIcon } from '@alfalab/icons-glyph/LogOutMIcon';
import { useNotifications, useReadNotification } from '../api/hooks';
import { useSession } from '../auth/session';
import { AlfaLogo } from './AlfaLogo';
import { companyInitials, companyShortName, formatRelative, ROLE_LABELS } from '../lib/format';
import { useToast } from '../components/Toasts';

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close();
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, close]);
  return ref;
}

const AVATAR_COLORS = ['#c8f06b', '#7b61ff', '#3fd0c9', '#ffb340', '#ff7ab6'];

export function CompanyAvatar({ title, index = 0, size = 36 }: { title: string; index?: number; size?: number }) {
  return (
    <span className="company-avatar" style={{ width: size, height: size, background: AVATAR_COLORS[index % AVATAR_COLORS.length] }}>
      {companyInitials(title)}
    </span>
  );
}

function CompanySwitcher() {
  const { company, companies, setCompanyId, me } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  if (!company) return null;
  const idx = companies.findIndex((c) => c.company_id === company.company_id);

  return (
    <div className="switcher" ref={ref}>
      <button className="switcher__btn" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <CompanyAvatar title={company.title} index={idx} />
        <span className="switcher__text">
          <span className="switcher__name">{companyShortName(company.title)}</span>
          <span className="switcher__sub">
            {ROLE_LABELS[company.role]} · {me?.display_name?.replace(/^Демо:\s*/, '')}
          </span>
        </span>
        <ChevronDownMIcon className="switcher__chevron" />
      </button>
      {open && (
        <div className="popover switcher__menu" role="menu">
          <div className="popover__title">Мои компании · {companies.length}</div>
          {companies.map((c, i) => (
            <button
              key={c.company_id}
              role="menuitem"
              className={`switcher__option ${c.company_id === company.company_id ? 'is-active' : ''}`}
              onClick={() => {
                setCompanyId(c.company_id);
                setOpen(false);
              }}
            >
              <CompanyAvatar title={c.title} index={i} size={32} />
              <span className="switcher__text">
                <span className="switcher__name">{companyShortName(c.title)}</span>
                <span className="switcher__sub">
                  {c.region} · {ROLE_LABELS[c.role]}
                </span>
              </span>
              {c.company_id === company.company_id && <CheckmarkMIcon className="switcher__check" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function NotificationsBell() {
  const { data } = useNotifications();
  const read = useReadNotification();
  const { setCompanyId } = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const unread = data?.unread ?? 0;

  return (
    <div className="bell" ref={ref}>
      <button className="icon-btn" aria-label={`Уведомления: ${unread} непрочитанных`} onClick={() => setOpen((o) => !o)}>
        <BellMIcon />
        {unread > 0 && <span className="icon-btn__dot">{unread > 9 ? '9+' : unread}</span>}
      </button>
      {open && (
        <div className="popover bell__menu">
          <div className="bell__head">
            <span className="popover__title">Уведомления</span>
            {unread > 0 && (
              <button className="link-btn" onClick={() => read.mutate('all')}>
                Прочитать все
              </button>
            )}
          </div>
          {!data?.items.length && <div className="empty-small">Новых уведомлений нет</div>}
          <div className="bell__list">
            {data?.items.map((n) => (
              <button
                key={n.id}
                className={`bell__item ${n.read ? '' : 'is-unread'}`}
                onClick={() => {
                  if (!n.read) read.mutate(n.id);
                  setOpen(false);
                  if (n.company_id) setCompanyId(n.company_id);
                  if (n.insight_id) navigate(`/analytics/insight/${n.insight_id}?via=notification`);
                  else if (n.kind === 'request_update') navigate('/analytics/requests');
                  else if (n.kind === 'digest') navigate('/analytics/digest');
                }}
              >
                <span className={`bell__kind bell__kind--${n.kind}`} />
                <span className="bell__body">
                  <span className="bell__title">{n.title}</span>
                  <span className="bell__text">{n.body}</span>
                  <span className="bell__time">{formatRelative(n.created_at)}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function UserPill() {
  const { me, logout } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const navigate = useNavigate();
  const name = me?.platform_role === 'admin' ? 'Администратор' : 'Альфа Босс';
  return (
    <div className="user" ref={ref}>
      <button className="user__pill" onClick={() => setOpen((o) => !o)}>
        <span className="user__avatar">{(me?.login ?? 'U').slice(0, 1).toUpperCase()}</span>
        {name}
      </button>
      {open && (
        <div className="popover user__menu">
          <div className="user__login">
            {me?.display_name}
            <span>логин: {me?.login}</span>
          </div>
          <button
            className="user__logout"
            onClick={() => {
              logout();
              navigate('/login');
            }}
          >
            <LogOutMIcon width={20} height={20} /> Выйти
          </button>
        </div>
      )}
    </div>
  );
}

export function TopBar() {
  const { isAdmin } = useSession();
  const toast = useToast();
  return (
    <header className="topbar">
      <Link to="/" className="topbar__brand">
        <AlfaLogo />
        <span>Альфа-Бизнес</span>
      </Link>
      <div className="topbar__right">
        <button className="icon-btn" aria-label="Письма" onClick={() => toast.show({ title: 'Письма из банка', body: 'Новых писем нет', ttl: 3000 })}>
          <MailMIcon />
        </button>
        {!isAdmin && <NotificationsBell />}
        {!isAdmin && <CompanySwitcher />}
        <UserPill />
      </div>
    </header>
  );
}
