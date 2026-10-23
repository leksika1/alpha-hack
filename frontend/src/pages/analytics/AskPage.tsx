// Вопрос по компании в целом (POST /ask): другой разрез, юрлицо группы, источник.
import { useSession } from '../../auth/session';
import { AskPanel } from '../../components/insight/AskPanel';
import { H2 } from '../../components/ui';

export function AskPage() {
  const { company } = useSession();
  if (!company) return null;
  return (
    <section className="card p-6">
      <H2>Вопрос аналитику</H2>
      <p className="mb-3 text-ink-2">Ответ строится по профилю выбранной компании, свежим событиям и материалам аналитики банка.</p>
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
