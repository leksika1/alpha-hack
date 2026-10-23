// Раздел «Аналитика» внутри интернет-банка: контекст компании (CJM 02), роль и вкладки.
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useSetRole } from '../../api/hooks';
import type { Role } from '../../api/types';
import { useSession } from '../../auth/session';
import { useToast } from '../../components/Toasts';
import { CompanyAvatar } from '../../layout/TopBar';
import { companyShortName, ROLE_FOCUS, ROLE_LABELS, segmentLabel } from '../../lib/format';
import { SparkIcon } from '../../layout/icons';

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

  if (meLoading) return <div className="page-loading">Загружаем ваши компании…</div>;
  if (!company) {
    return (
      <div className="card card--pad">
        <h2>Нет доступных компаний</h2>
        <p className="muted">У этой учётной записи нет компаний с доступом к аналитике.</p>
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
    <div className="analytics">
      <div className="analytics__head">
        <div className="analytics__title">
          <span className="analytics__eyebrow">
            <SparkIcon width={16} height={16} /> Альфа-Аналитика
          </span>
          <h1>Аналитика для вашей компании</h1>
        </div>
      </div>

      <div className="context-bar card">
        <CompanyAvatar title={company.title} index={idx} size={44} />
        <div className="context-bar__main">
          <div className="context-bar__label">Анализ подготовлен для</div>
          <div className="context-bar__name">
            {companies.length > 1 ? (
              <select
                className="inline-select"
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
          <div className="context-bar__meta">
            ОКВЭД {company.okved_code} · {company.region} · {segmentLabel(company.segment)}
            <button className="link-btn" onClick={() => navigate('/analytics/profile')}>
              Что мы знаем о компании
            </button>
          </div>
        </div>
        <div className="context-bar__role">
          <label className="context-bar__label" htmlFor="role-select">
            Ваша роль
          </label>
          <select
            id="role-select"
            className="role-select"
            value={company.role}
            disabled={setRole.isPending}
            onChange={(e) => changeRole(e.target.value as Role)}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABELS[r]}
              </option>
            ))}
          </select>
          <div className="context-bar__focus">Фокус: {ROLE_FOCUS[company.role]}</div>
        </div>
      </div>

      <nav className="tabs" aria-label="Разделы аналитики">
        {TABS.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => `tabs__item ${isActive ? 'is-active' : ''}`}>
            {t.label}
          </NavLink>
        ))}
      </nav>

      <Outlet />
    </div>
  );
}
