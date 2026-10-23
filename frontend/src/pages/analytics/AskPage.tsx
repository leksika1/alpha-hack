// Вопрос по компании в целом (POST /ask): другой разрез, юрлицо группы, источник.
import { useSession } from '../../auth/session';
import { AskPanel } from '../../components/insight/AskPanel';

export function AskPage() {
  const { company } = useSession();
  if (!company) return null;
  return (
    <section className="card card--pad">
      <h2 className="h2">Вопрос аналитику</h2>
      <p className="muted">Ответ строится по профилю выбранной компании, свежим событиям и материалам аналитики банка.</p>
      <AskPanel
        companyId={company.company_id}
        suggestions={[
          'Какие события недели сильнее всего влияют на нашу компанию?',
          'Как ставка ЦБ скажется на наших кредитах?',
          'Есть ли риски у наших ключевых контрагентов?',
        ]}
      />
    </section>
  );
}
