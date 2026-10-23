// Профиль компании (CJM 02): что сервис знает, источник каждого признака, пробелы, анкета и связи компании.
import { useEffect, useState } from 'react';
import { Button } from '@alfalab/core-components/button';
import { useGraph, useProfile, useUpdateProfile } from '../../api/hooks';
import type { GraphEdge, ProfileSection } from '../../api/types';
import { useSession } from '../../auth/session';
import { CardSkeleton, ErrorBox } from '../../components/ui';
import { useToast } from '../../components/Toasts';
import { featureLabel, featureValue, FEATURE_SOURCE, formatMoney, formatPercent, industryTag } from '../../lib/format';

const HIDDEN_PATHS = /industry_share_|top_in|top_out|group\.id$|client_type|shared_person_companies/;
const CURRENCIES = ['CNY', 'USD', 'EUR', 'KZT', 'TRY', 'AED', 'INR', 'BYN'];

type Feature = ProfileSection['features'][number];

interface TopCounterparty {
  counterparty_id: string;
  known: boolean;
  share: number;
  region: string | null;
  amount_rub: number;
  okved_name: string | null;
  same_group: boolean;
}

export function ProfilePage() {
  const { company } = useSession();
  const profile = useProfile(company?.company_id);
  const graph = useGraph(company?.company_id);

  if (profile.isLoading) return <CardSkeleton rows={6} />;
  if (profile.isError || !profile.data) return <ErrorBox error={profile.error} onRetry={() => profile.refetch()} />;

  const p = profile.data;
  const completeness = p.completeness as { score?: number; missing?: string[]; stubs?: string[]; user_fields?: string[] };
  const all = p.sections.flatMap((s) => s.features);
  const find = (path: string) => all.find((f) => f.path === path);
  const topIn = (find('counterparties.top_in')?.value ?? []) as TopCounterparty[];
  const topOut = (find('counterparties.top_out')?.value ?? []) as TopCounterparty[];

  return (
    <div className="profile">
      <section className="card card--pad profile__summary">
        <div>
          <h2 className="h2">Что мы знаем о компании</h2>
          <p className="muted">
            Профиль собран автоматически из данных банка — анкету заполнять не нужно. Проверьте его и при желании дополните: это повысит
            точность отбора событий.
          </p>
        </div>
        <div className="completeness">
          <div className="completeness__ring" style={{ ['--p' as string]: `${Math.round((completeness.score ?? 0) * 100)}` }}>
            <span>{Math.round((completeness.score ?? 0) * 100)}%</span>
          </div>
          <div className="small">
            Полнота профиля
            {!!completeness.missing?.length && (
              <div className="text-attention">Нет данных: {completeness.missing.map(featureLabel).join(', ')}</div>
            )}
            {!!completeness.stubs?.length && <div className="text-attention">Заглушки в данных: {completeness.stubs.map(featureLabel).join(', ')}</div>}
          </div>
        </div>
      </section>

      <div className="profile__grid">
        {p.sections.map((s) => {
          const features = s.features.filter((f) => !HIDDEN_PATHS.test(f.path));
          if (!features.length && s.key !== 'counterparties') return null;
          return (
            <section key={s.key} className="card card--pad">
              <h3 className="h3">{s.title}</h3>
              <dl className="features">
                {features.map((f) => (
                  <FeatureRow key={f.path} f={f} />
                ))}
              </dl>
              {s.key === 'counterparties' && (
                <>
                  <CounterpartyTable title="Крупнейшие плательщики" rows={topIn} />
                  <CounterpartyTable title="Крупнейшие получатели" rows={topOut} />
                </>
              )}
            </section>
          );
        })}
      </div>

      {company && <ProfileForm companyId={company.company_id} userProvided={p.user_provided as Record<string, unknown>} />}

      <section className="card card--pad">
        <h3 className="h3">Связи компании</h3>
        {graph.isLoading && <p className="muted">Загружаем граф связей…</p>}
        {graph.data && <GraphView edges={graph.data.edges} group={graph.data.group as Record<string, unknown> | null} unknown={graph.data.unknown_counterparties as Record<string, number>} />}
      </section>

      <p className="muted small">
        Источники признаков:{' '}
        {Object.entries(p.sources_legend as Record<string, string>)
          .map(([k, v]) => `${FEATURE_SOURCE[k] ?? k} — ${v}`)
          .join('; ')}
        . Версия контекста {p.context_version}.
      </p>
    </div>
  );
}

function FeatureRow({ f }: { f: Feature }) {
  return (
    <>
      <dt>{featureLabel(f.path)}</dt>
      <dd>
        <span className={f.value === false ? 'muted' : ''}>{featureValue(f.path, f.value)}</span>
        <span className={`src-tag src-tag--${f.source}`} title={f.note ?? undefined}>
          {FEATURE_SOURCE[f.source] ?? f.source}
        </span>
        {f.note && <div className="features__note">{f.note}</div>}
      </dd>
    </>
  );
}

function CounterpartyTable({ title, rows }: { title: string; rows: TopCounterparty[] }) {
  if (!rows.length) return null;
  return (
    <div className="cp">
      <div className="cp__title">{title}</div>
      {rows.slice(0, 5).map((r) => (
        <div key={r.counterparty_id} className="cp__row">
          <div className="cp__name">
            {r.okved_name ?? 'Контрагент вне базы'}
            <span className="muted small">
              {' '}
              · {r.region ?? 'регион неизвестен'}
              {r.same_group && ' · своя группа'}
            </span>
          </div>
          <div className="cp__bar">
            <span style={{ width: `${Math.min(100, r.share * 100)}%` }} />
          </div>
          <div className="cp__val">
            {formatPercent(r.share)} · {formatMoney(r.amount_rub)}
          </div>
        </div>
      ))}
    </div>
  );
}

function splitList(s: string): string[] {
  return s
    .split(/[,;\n]/)
    .map((x) => x.trim())
    .filter(Boolean);
}

function ProfileForm({ companyId, userProvided }: { companyId: string; userProvided: Record<string, unknown> }) {
  const update = useUpdateProfile(companyId);
  const toast = useToast();
  const asText = (k: string) => (Array.isArray(userProvided?.[k]) ? (userProvided[k] as string[]).join(', ') : ((userProvided?.[k] as string) ?? ''));
  const [suppliers, setSuppliers] = useState('');
  const [customers, setCustomers] = useState('');
  const [materials, setMaterials] = useState('');
  const [currencies, setCurrencies] = useState<string[]>([]);
  const [credit, setCredit] = useState('');
  const [notes, setNotes] = useState('');

  useEffect(() => {
    setSuppliers(asText('suppliers'));
    setCustomers(asText('customers'));
    setMaterials(asText('raw_materials'));
    setCurrencies(Array.isArray(userProvided?.currencies) ? (userProvided.currencies as string[]) : []);
    setCredit(asText('credit_plans'));
    setNotes(asText('notes'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, JSON.stringify(userProvided)]);

  const submit = () =>
    update.mutate(
      {
        suppliers: splitList(suppliers),
        customers: splitList(customers),
        raw_materials: splitList(materials),
        currencies,
        credit_plans: credit || null,
        notes: notes || null,
      },
      {
        onSuccess: (res) =>
          toast.show({
            title: 'Профиль дополнен',
            body:
              (res as { regeneration?: string }).regeneration === 'queued'
                ? `Релевантность пересчитана, инсайты обновляются (${String((res as { candidates_pending?: number }).candidates_pending ?? 0)} в очереди).`
                : 'Данные сохранены и учитываются при отборе событий.',
            tone: 'positive',
          }),
        onError: (e) => toast.show({ title: 'Не удалось сохранить', body: e.message, tone: 'error' }),
      },
    );

  return (
    <section className="card card--pad profile-form">
      <h3 className="h3">Дополнить профиль</h3>
      <p className="muted small">Необязательно. Только то, чего нет в данных банка: так мы точнее поймём, какие события касаются вас.</p>
      <div className="form-grid">
        <label className="field">
          <span>Ключевые поставщики (отрасли, страны)</span>
          <input value={suppliers} onChange={(e) => setSuppliers(e.target.value)} placeholder="металлургия, Китай" />
        </label>
        <label className="field">
          <span>Ключевые покупатели</span>
          <input value={customers} onChange={(e) => setCustomers(e.target.value)} placeholder="строительство, девелоперы" />
        </label>
        <label className="field">
          <span>Сырьё и материалы</span>
          <input value={materials} onChange={(e) => setMaterials(e.target.value)} placeholder="сталь, дизельное топливо" />
        </label>
        <label className="field">
          <span>Планы по кредитам и ВЭД</span>
          <input value={credit} onChange={(e) => setCredit(e.target.value)} placeholder="рефинансирование в I квартале" />
        </label>
      </div>
      <div className="field">
        <span>Валюты расчётов</span>
        <div className="chips">
          {CURRENCIES.map((c) => (
            <button
              key={c}
              type="button"
              className={`chip ${currencies.includes(c) ? 'is-active' : ''}`}
              onClick={() => setCurrencies((xs) => (xs.includes(c) ? xs.filter((x) => x !== c) : [...xs, c]))}
            >
              {c}
            </button>
          ))}
        </div>
      </div>
      <label className="field">
        <span>Комментарий</span>
        <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} />
      </label>
      <Button view="primary" size={40} loading={update.isPending} onClick={submit}>
        Сохранить и пересчитать ленту
      </Button>
    </section>
  );
}

const EDGE_LABELS: Record<string, string> = {
  same_group: 'Своя группа',
  payment_out: 'Платим мы',
  payment_in: 'Платят нам',
  shared_person: 'Общие физлица',
  key_customer: 'Вероятный ключевой покупатель',
  key_supplier: 'Вероятный ключевой поставщик',
  likely_supplier: 'Вероятный поставщик',
  likely_customer: 'Вероятный покупатель',
  affiliated_outside_group: 'Возможная аффилированность',
};

function GraphView({ edges, group, unknown }: { edges: GraphEdge[]; group: Record<string, unknown> | null; unknown: Record<string, number> }) {
  const [onlyHyp, setOnlyHyp] = useState(false);
  const list = edges.filter((e) => (onlyHyp ? e.is_hypothesis : true)).slice(0, 14);
  return (
    <div className="graph">
      <div className="graph__summary">
        {group && (
          <div className="graph__stat">
            <b>{String(group.size)}</b> юрлиц в группе · выручка группы {formatMoney(Number(group.revenue_rub ?? 0))}
            {group.industries ? (
              <span className="muted small"> · отрасли: {Object.keys(group.industries as object).join(', ')}</span>
            ) : null}
          </div>
        )}
        {unknown && (
          <div className="muted small">
            Оборот с контрагентами вне базы: входящий {formatPercent(unknown.share_in ?? 0)}, исходящий {formatPercent(unknown.share_out ?? 0)} — их
            профиль неизвестен и не достраивается.
          </div>
        )}
        <label className="toggle">
          <input type="checkbox" checked={onlyHyp} onChange={(e) => setOnlyHyp(e.target.checked)} /> Только гипотезы о связях
        </label>
      </div>
      <div className="edges">
        {list.map((e, i) => {
          const cp = e.counterparty as Record<string, unknown>;
          const ev = e.evidence as Record<string, unknown> | null;
          return (
            <div key={i} className={`edge ${e.is_hypothesis ? 'edge--hyp' : ''}`}>
              <div className="edge__head">
                <span className="edge__label">{EDGE_LABELS[e.label] ?? EDGE_LABELS[e.edge_type] ?? e.label}</span>
                {e.is_hypothesis && <span className="kind kind--hypothesis">Гипотеза · уверенность {Math.round((e.confidence ?? 0) * 100)}%</span>}
              </div>
              <div className="edge__cp">
                {String(cp?.okved_name ?? 'Контрагент вне базы')}
                <span className="muted small"> · {String(cp?.region ?? '')}</span>
              </div>
              <div className="muted small">
                {e.amount_rub ? `${formatMoney(e.amount_rub)}` : ''}
                {e.share ? ` · ${formatPercent(e.share)} оборота` : ''}
                {ev?.statement ? ` · ${String(ev.statement)}` : ''}
                {Array.isArray(ev?.basis) ? ` (основание: ${(ev!.basis as string[]).map(industryTag).join(', ')})` : ''}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
