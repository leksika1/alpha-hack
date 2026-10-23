// Раздел «Аналитика» внутри интернет-банка: контекст компании (CJM 02), роль и вкладки.
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useSetRole } from '../../api/hooks';
import type { Role } from '../../api/types';
import { useSession } from '../../auth/session';
import { useToast } from '../../components/Toasts';
import { LinkButton, selectClass } from '../../components/ui';
import { CompanyAvatar } from '../../layout/TopBar';
import { companyShortName, ROLE_FOCUS, ROLE_LABELS, segmentLabel } from '../../lib/format';
import { SparkIcon } from '../../layout/icons';
import { cn } from '../../lib/cn';

const TABS = [
  { to: '/analytics', label: 'Лента инсайтов', end: true },
  { to: '/analytics/digest', label: 'Дайджест' },
  { to: '/analytics/ask', label: 'Вопрос аналитику' },
  { to: '/analytics/profile', label: 'Профиль компании' },
  { to: '/analytics/materials', label: 'Материалы банка' },
  { to: '/analytics/requests', label: 'Мои заявки' },
];

const ROLES: Role[] = ['owner', 'ceo', 'cfo', 'treasurer', 'chief_accountant', 'other'];

export function AnalyticsLayout() {
  const { company, companies, setCompanyId, meLoading } = useSession();
  const setRole = useSetRole();
  const toast = useToast();
  const navigate = useNavigate();

  if (meLoading) return <div className="p-10 text-ink-2">Загружаем ваши компании…</div>;
  if (!company) {
    return (
      <div className="card p-6">
        <h2>Нет доступных компаний</h2>
        <p className="text-ink-2">У этой учётной записи нет компаний с доступом к аналитике.</p>
      </div>
    );
  }
  const idx = companies.findIndex((c) => c.company_id === company.company_id);

  const changeRole = (role: Role) => {
    setRole.mutate(
      { companyId: company.company_id, role },
      {
        onSuccess: () =>
          toast.show({
            title: `Роль: ${ROLE_LABELS[role]}`,
            body: `Порядок ленты и акценты инсайтов перестроены под задачи роли — ${ROLE_FOCUS[role]}.`,
            tone: 'positive',
          }),
        onError: (e) => toast.show({ title: 'Не удалось сменить роль', body: e.message, tone: 'error' }),
      },
    );
  };

  return (
    <div>
      <div className="mt-1 mb-4">
        <span className="eyebrow">
          <SparkIcon width={16} height={16} /> Альфа-Аналитика
        </span>
        <h1 className="mt-1 text-[28px]">Аналитика для вашей компании</h1>
      </div>

      <div className="card flex flex-col items-start gap-4 px-5 py-4 md:flex-row md:items-center">
        <CompanyAvatar title={company.title} index={idx} size={44} />
        <div className="min-w-0 flex-1">
          <div className="text-xs text-ink-2">Анализ подготовлен для</div>
          <div className="text-[17px] font-bold">
            {companies.length > 1 ? (
              <select
                className="max-w-full cursor-pointer bg-transparent p-0 text-[17px] font-bold outline-none"
                value={company.company_id}
                onChange={(e) => setCompanyId(e.target.value)}
                aria-label="Компания"
              >
                {companies.map((c) => (
                  <option key={c.company_id} value={c.company_id}>
                    {companyShortName(c.title)}
                  </option>
                ))}
              </select>
            ) : (
              companyShortName(company.title)
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-[13px] text-ink-2">
            ОКВЭД {company.okved_code} · {company.region} · {segmentLabel(company.segment)}
            <LinkButton onClick={() => navigate('/analytics/profile')}>Что мы знаем о компании</LinkButton>
          </div>
        </div>
        <div className="w-full md:w-60">
          <label className="text-xs text-ink-2" htmlFor="role-select">
            Ваша роль
          </label>
          <select id="role-select" className={cn(selectClass, 'w-full bg-muted')} value={company.role} disabled={setRole.isPending} onChange={(e) => changeRole(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
          <div className="mt-1 text-xs text-ink-2">Фокус: {ROLE_FOCUS[company.role]}</div>
        </div>
      </div>

      <nav className="mt-5 mb-4 flex gap-1 overflow-x-auto border-b border-line" aria-label="Разделы аналитики">
        {TABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) =>
              cn(
                '-mb-px border-b-2 px-3.5 py-2.5 font-semibold whitespace-nowrap hover:text-ink hover:no-underline',
                isActive ? 'border-accent text-ink' : 'border-transparent text-ink-2',
              )
            }
          >
            {t.label}
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  );
}
