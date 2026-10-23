import { useEffect, useRef, useState, type ReactNode } from 'react';
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
import { cn } from '../lib/cn';

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
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-[10px] text-[13px] font-extrabold text-night"
      style={{ width: size, height: size, background: AVATAR_COLORS[index % AVATAR_COLORS.length] }}
    >
      {companyInitials(title)}
    </span>
  );
}

export const iconBtn =
  'relative inline-flex size-10 items-center justify-center rounded-full text-ink-2 hover:bg-muted-2 hover:text-ink [&_svg]:size-[22px]';

function Popover({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('absolute top-[calc(100%+8px)] right-0 z-50 origin-top-right rounded-tile bg-white p-3 shadow-pop motion-safe:animate-pop-in', className)}>{children}</div>;
}

const popTitle = 'px-2 pt-1 pb-2 text-[13px] font-bold text-ink-2';

function CompanySwitcher() {
  const { company, companies, setCompanyId, me } = useSession();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  if (!company) return null;
  const idx = companies.findIndex((c) => c.company_id === company.company_id);

  return (
    <div className="relative" ref={ref}>
      <button className="flex max-w-[520px] items-center gap-2.5 rounded-[14px] px-2.5 py-1.5 text-left hover:bg-muted-2" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <CompanyAvatar title={company.title} index={idx} />
        <span className="hidden min-w-0 flex-col md:flex">
          <span className="text-sm leading-tight font-semibold">{companyShortName(company.title)}</span>
          <span className="truncate text-xs text-ink-2">
            {ROLE_LABELS[company.role]}, {me?.display_name?.replace(/^Демо:\s*/, '')}
          </span>
        </span>
        <ChevronDownMIcon className="size-[18px] shrink-0 text-ink-3" />
      </button>
      {open && (
        <Popover className="w-[460px]">
          <div className={popTitle}>Мои компании ({companies.length})</div>
          {companies.map((c, i) => {
            const active = c.company_id === company.company_id;
            return (
              <button
                key={c.company_id}
                role="menuitem"
                className={cn('flex w-full items-center gap-2.5 rounded-xl p-2 text-left hover:bg-muted', active && 'bg-muted')}
                onClick={() => {
                  setCompanyId(c.company_id);
                  setOpen(false);
                }}
              >
                <CompanyAvatar title={c.title} index={i} size={32} />
                <span className="flex min-w-0 flex-col">
                  <span className="text-sm leading-tight font-semibold">{companyShortName(c.title)}</span>
                  <span className="truncate text-xs text-ink-2">
                    {c.region}, {ROLE_LABELS[c.role].toLowerCase()}
                  </span>
                </span>
                {active && <CheckmarkMIcon className="ml-auto shrink-0 text-accent" />}
              </button>
            );
          })}
        </Popover>
      )}
    </div>
  );
}

const KIND_DOT: Record<string, string> = { urgent_insight: 'bg-accent', request_update: 'bg-info' };

function NotificationsBell() {
  const { data } = useNotifications();
  const read = useReadNotification();
  const { setCompanyId } = useSession();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const unread = data?.unread ?? 0;

  return (
    <div className="relative" ref={ref}>
      <button className={iconBtn} aria-label={`Уведомления: ${unread} непрочитанных`} onClick={() => setOpen((o) => !o)}>
        <BellMIcon />
        {unread > 0 && (
          <span className="absolute top-0.5 right-0 h-[18px] min-w-[18px] rounded-[9px] bg-accent px-[5px] text-[11px] leading-[18px] font-bold text-white">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>
      {open && (
        <Popover className="w-[420px]">
          <div className="flex items-center justify-between pr-2">
            <span className={popTitle}>Уведомления</span>
            {unread > 0 && (
              <button className="text-info hover:underline" onClick={() => read.mutate('all')}>
                Прочитать все
              </button>
            )}
          </div>
          {!data?.items.length && <div className="px-2 py-4 text-ink-2">Новых уведомлений нет</div>}
          <div className="max-h-[440px] overflow-auto">
            {data?.items.map((n) => (
              <button
                key={n.id}
                className="flex w-full gap-2.5 rounded-xl px-2 py-2.5 text-left hover:bg-muted"
                onClick={() => {
                  if (!n.read) read.mutate(n.id);
                  setOpen(false);
                  if (n.company_id) setCompanyId(n.company_id);
                  if (n.insight_id) navigate(`/analytics/insight/${n.insight_id}?via=notification`);
                  else if (n.kind === 'request_update') navigate('/analytics/requests');
                  else if (n.kind === 'digest') navigate('/analytics/digest');
                }}
              >
                <span className={cn('mt-[7px] size-2 shrink-0 rounded-full', KIND_DOT[n.kind] ?? 'bg-ink-3')} />
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className={cn('text-sm', !n.read && 'font-bold')}>{n.title}</span>
                  <span className="line-clamp-2 text-[13px] text-ink-2">{n.body}</span>
                  <span className="text-xs text-ink-3">{formatRelative(n.created_at)}</span>
                </span>
              </button>
            ))}
          </div>
        </Popover>
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
    <div className="relative" ref={ref}>
      <button className="flex items-center gap-2 rounded-3xl bg-night py-1.5 pr-4 pl-1.5 text-[13px] font-semibold text-white" onClick={() => setOpen((o) => !o)}>
        <span className="inline-flex size-8 items-center justify-center rounded-full bg-linear-135 from-accent to-[#8a1a12] font-bold">
          {(me?.login ?? 'U').slice(0, 1).toUpperCase()}
        </span>
        {name}
      </button>
      {open && (
        <Popover className="w-[260px]">
          <div className="flex flex-col gap-0.5 px-2 pt-1 pb-3 font-semibold">
            {me?.display_name}
            <span className="text-[13px] font-normal text-ink-2">логин: {me?.login}</span>
          </div>
          <button
            className="flex w-full items-center gap-2 rounded-xl bg-muted px-3 py-2.5"
            onClick={() => {
              logout();
              navigate('/login');
            }}
          >
            <LogOutMIcon width={20} height={20} /> Выйти
          </button>
        </Popover>
      )}
    </div>
  );
}

export function TopBar() {
  const { isAdmin } = useSession();
  const toast = useToast();
  return (
    <header className="sticky top-0 z-20 flex h-[72px] items-center justify-between bg-page pr-6 pl-5">
      <Link to="/" className="flex items-center gap-3 text-[17px] font-bold text-ink hover:no-underline">
        <AlfaLogo />
        <span className="hidden md:inline">Альфа-Бизнес</span>
      </Link>
      <div className="flex items-center gap-2">
        <button className={iconBtn} aria-label="Письма" onClick={() => toast.show({ title: 'Письма из банка', body: 'Новых писем нет', ttl: 3000 })}>
          <MailMIcon />
        </button>
        {!isAdmin && <NotificationsBell />}
        {!isAdmin && <CompanySwitcher />}
        <UserPill />
      </div>
    </header>
  );
}
