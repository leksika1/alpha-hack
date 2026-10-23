// Мои заявки из инсайтов и их статусы в мок-CRM (CJM 07).
import { Link } from 'react-router-dom';
import { useRequests } from '../../api/hooks';
import { CardSkeleton, EmptyState, ErrorBox, Pill } from '../../components/ui';
import { ACTION_TYPES, formatDate, REQUEST_STATUS } from '../../lib/format';
import { useSession } from '../../auth/session';

const th = 'border-b border-line px-4 py-3.5 text-left text-xs font-semibold text-ink-2';
const td = 'border-b border-line px-4 py-3.5 align-top';

export function RequestsPage() {
  const requests = useRequests();
  const { companies } = useSession();
  const title = (id: string) => companies.find((c) => c.company_id === id)?.title.split(' · ')[0] ?? id;

  if (requests.isLoading) return <CardSkeleton />;
  if (requests.isError) return <ErrorBox error={requests.error} onRetry={() => requests.refetch()} />;
  if (!requests.data?.length) return <EmptyState title="Заявок пока нет" text="Оформите обращение к менеджеру или звонок из любого инсайта — статус появится здесь." />;

  return (
    <section className="card overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={th}>Обращение</th>
            <th className={th}>Инсайт</th>
            <th className={th}>Компания</th>
            <th className={th}>Создано</th>
            <th className={th}>Статус</th>
          </tr>
        </thead>
        <tbody className="[&_tr:last-child_td]:border-b-0">
          {requests.data.map((r) => (
            <tr key={r.request_id}>
              <td className={td}>
                <b>{ACTION_TYPES[r.request_type] ?? r.request_type}</b>
                <div className="text-[13px] text-ink-2">{r.external_ref}</div>
              </td>
              <td className={td}>{r.insight_id ? <Link to={`/analytics/insight/${r.insight_id}?via=requests`}>{r.insight_title}</Link> : '—'}</td>
              <td className={`${td} text-[13px]`}>{title(r.company_id)}</td>
              <td className={`${td} text-[13px]`}>{formatDate(r.updated_at ?? r.created_at, true)}</td>
              <td className={td}>
                <Pill tone={REQUEST_STATUS[r.status]?.color ?? 'grey'}>{REQUEST_STATUS[r.status]?.label ?? r.status}</Pill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
