// Мои заявки из инсайтов и их статусы в мок-CRM (CJM 07).
import { Link } from 'react-router-dom';
import { useRequests } from '../../api/hooks';
import { CardSkeleton, EmptyState, ErrorBox } from '../../components/ui';
import { ACTION_TYPES, formatDate, REQUEST_STATUS } from '../../lib/format';
import { useSession } from '../../auth/session';

export function RequestsPage() {
  const requests = useRequests();
  const { companies } = useSession();
  const title = (id: string) => companies.find((c) => c.company_id === id)?.title.split(' · ')[0] ?? id;

  if (requests.isLoading) return <CardSkeleton />;
  if (requests.isError) return <ErrorBox error={requests.error} onRetry={() => requests.refetch()} />;
  if (!requests.data?.length)
    return <EmptyState title="Заявок пока нет" text="Оформите обращение к менеджеру или звонок из любого инсайта — статус появится здесь." />;

  return (
    <section className="card">
      <table className="table">
        <thead>
          <tr>
            <th>Обращение</th>
            <th>Инсайт</th>
            <th>Компания</th>
            <th>Создано</th>
            <th>Статус</th>
          </tr>
        </thead>
        <tbody>
          {requests.data.map((r) => (
            <tr key={r.request_id}>
              <td>
                <b>{ACTION_TYPES[r.request_type] ?? r.request_type}</b>
                <div className="muted small">{r.external_ref}</div>
              </td>
              <td>{r.insight_id ? <Link to={`/analytics/insight/${r.insight_id}?via=requests`}>{r.insight_title}</Link> : '—'}</td>
              <td className="small">{title(r.company_id)}</td>
              <td className="small">{formatDate(r.updated_at ?? r.created_at, true)}</td>
              <td>
                <span className={`pill pill--${REQUEST_STATUS[r.status]?.color ?? 'grey'}`}>{REQUEST_STATUS[r.status]?.label ?? r.status}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
