// Профиль компании (CJM 02): что сервис знает, источник каждого признака, пробелы, анкета и связи компании.
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { Button } from '@alfalab/core-components/button';
import { useGraph, useProfile, useUpdateProfile } from '../../api/hooks';
import type { GraphEdge, ProfileSection } from '../../api/types';
import { useSession } from '../../auth/session';
import { CardSkeleton, Chip, ErrorBox, H2, H3, KindBadge } from '../../components/ui';
import { useToast } from '../../components/Toasts';
import { capitalizeFirst, featureLabel, featureValue, FEATURE_SOURCE, formatMoney, formatPercent, industryTag } from '../../lib/format';
import { cn } from '../../lib/cn';

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

const SRC_TAG: Record<string, string> = {
  derived: 'bg-purple-bg text-purple',
  user: 'bg-pos-bg text-pos',
};

export function ProfilePage() {
  const { company } = useSession();
  const profile = useProfile(company?.company_id);
  const graph = useGraph(company?.company_id);

  if (profile.isLoading) return <CardSkeleton rows={6} />;
  if (profile.isError || !profile.data) return <ErrorBox error={profile.error} onRetry={() => profile.refetch()} />;

  const p = profile.data;
  const completeness = p.completeness as { score?: number; missing?: string[]; stubs?: string[]; user_fields?: string[] };
  const score = Math.round((completeness.score ?? 0) * 100);
  const all = p.sections.flatMap((s) => s.features);
  const find = (path: string) => all.find((f) => f.path === path);
  const topIn = (find('counterparties.top_in')?.value ?? []) as TopCounterparty[];
  const topOut = (find('counterparties.top_out')?.value ?? []) as TopCounterparty[];

  return (
    <div>
      <section className="stagger card flex flex-col items-start justify-between gap-6 p-6 md:flex-row md:items-center">
        <div>
          <H2>Что мы знаем о компании</H2>
          <p className="text-ink-2">
            Профиль собран автоматически из данных банка — анкету заполнять не нужно. Проверьте его и при желании дополните: это повысит точность отбора
            событий.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <div
            className="flex size-16 items-center justify-center rounded-full"
            style={{ background: `conic-gradient(var(--color-pos) ${score}%, var(--color-muted-2) 0)` }}
          >
            <span className="flex size-[52px] items-center justify-center rounded-full bg-white text-sm font-bold">{score}%</span>
          </div>
          <div className="text-[13px]">
            Полнота профиля
            {!!completeness.missing?.length && <div className="text-att">Нет данных: {completeness.missing.map(featureLabel).join(', ')}</div>}
            {!!completeness.stubs?.length && <div className="text-att">Заглушки в данных: {completeness.stubs.map(featureLabel).join(', ')}</div>}
          </div>
        </div>
      </section>

      <div className="my-4 grid gap-4 md:grid-cols-2">
        {p.sections.map((s, si) => {
          const features = s.features.filter((f) => !HIDDEN_PATHS.test(f.path));
          if (!features.length && s.key !== 'counterparties') return null;
          return (
            <section key={s.key} style={{ '--i': si + 1 } as CSSProperties} className="stagger card p-6">
              <H3>{s.title}</H3>
              <dl className="grid grid-cols-[minmax(120px,0.9fr)_1.1fr] gap-x-3 gap-y-2 text-sm">
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

      <section style={{ '--i': 10 } as CSSProperties} className="stagger card mt-4 p-6">
        <H3>Связи компании</H3>
        {graph.isLoading && <p className="text-ink-2">Загружаем граф связей…</p>}
        {graph.data && (
          <GraphView edges={graph.data.edges} group={graph.data.group as Record<string, unknown> | null} unknown={graph.data.unknown_counterparties as Record<string, number>} />
        )}
      </section>

      <p className="mt-4 text-[13px] text-ink-2">
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
      <dt className="text-ink-2">{featureLabel(f.path)}</dt>
      <dd>
        <span className={cn(f.value === false && 'text-ink-2')}>{featureValue(f.path, f.value)}</span>
        <span className={cn('ml-1.5 rounded-md px-1.5 py-px text-[11px] whitespace-nowrap', SRC_TAG[f.source] ?? 'bg-muted text-ink-2')} title={f.note ?? undefined}>
          {FEATURE_SOURCE[f.source] ?? f.source}
        </span>
        {f.note && <div className="text-xs text-ink-3">{f.note}</div>}
      </dd>
    </>
  );
}

function CounterpartyTable({ title, rows }: { title: string; rows: TopCounterparty[] }) {
  if (!rows.length) return null;
  return (
    <div className="mt-4">
      <div className="mb-1.5 text-sm font-bold">{title}</div>
      {rows.slice(0, 5).map((r) => (
        <div key={r.counterparty_id} className="grid grid-cols-[1fr_80px_150px] items-center gap-2.5 py-1 text-[13px]">
          <div>
            {r.okved_name ?? 'Контрагент вне базы'}
            <span className="text-ink-2">
              {' '}
              , {r.region ?? 'регион неизвестен'}
              {r.same_group && ', своя группа'}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-sm bg-muted-2">
            <span className="block h-full bg-night" style={{ width: `${Math.min(100, r.share * 100)}%` }} />
          </div>
          <div className="text-right text-ink-2">
            {formatPercent(r.share)}, {formatMoney(r.amount_rub)}
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex w-full flex-col gap-1.5 text-[13px] text-ink-2">
      <span>{label}</span>
      {children}
    </label>
  );
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
    <section style={{ '--i': 9 } as CSSProperties} className="stagger card flex flex-col items-start gap-3 p-6">
      <h3 className="mt-1 text-[17px]">Дополнить профиль</h3>
      <p className="text-[13px] text-ink-2">Необязательно. Только то, чего нет в данных банка: так мы точнее поймём, какие события касаются вас.</p>
      <div className="grid w-full gap-3 md:grid-cols-2">
        <Field label="Ключевые поставщики (отрасли, страны)">
          <input className="field-input" value={suppliers} onChange={(e) => setSuppliers(e.target.value)} placeholder="металлургия, Китай" />
        </Field>
        <Field label="Ключевые покупатели">
          <input className="field-input" value={customers} onChange={(e) => setCustomers(e.target.value)} placeholder="строительство, девелоперы" />
        </Field>
        <Field label="Сырьё и материалы">
          <input className="field-input" value={materials} onChange={(e) => setMaterials(e.target.value)} placeholder="сталь, дизельное топливо" />
        </Field>
        <Field label="Планы по кредитам и ВЭД">
          <input className="field-input" value={credit} onChange={(e) => setCredit(e.target.value)} placeholder="рефинансирование в I квартале" />
        </Field>
      </div>
      <div className="flex w-full flex-col gap-1.5 text-[13px] text-ink-2">
        <span>Валюты расчётов</span>
        <div className="flex flex-wrap gap-2">
          {CURRENCIES.map((c) => (
            <Chip key={c} active={currencies.includes(c)} onClick={() => setCurrencies((xs) => (xs.includes(c) ? xs.filter((x) => x !== c) : [...xs, c]))}>
              {c}
            </Chip>
          ))}
        </div>
      </div>
      <Field label="Комментарий">
        <textarea className="field-input" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} />
      </Field>
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
    <div>
      <div className="mb-3 flex flex-col gap-1.5">
        {group && (
          <div>
            <b>{String(group.size)}</b> юрлиц в группе, выручка группы {formatMoney(Number(group.revenue_rub ?? 0))}
            {group.industries ?  <span className="text-[13px] text-ink-2">Отрасли: {Object.keys(group.industries as object).join(', ')}</span> : null}
          </div>
        )}
        {unknown && (
          <div className="text-[13px] text-ink-2">
            Оборот с контрагентами вне базы: входящий {formatPercent(unknown.share_in ?? 0)}, исходящий {formatPercent(unknown.share_out ?? 0)} — их профиль
            неизвестен и не достраивается.
          </div>
        )}
        <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" className="accent-accent" checked={onlyHyp} onChange={(e) => setOnlyHyp(e.target.checked)} /> Только гипотезы о связях
        </label>
      </div>
      <div className="grid gap-2.5 md:grid-cols-2">
        {list.map((e, i) => {
          const cp = e.counterparty as Record<string, unknown>;
          const ev = e.evidence as Record<string, unknown> | null;
          return (
            <div
              key={i}
              style={{ '--i': Math.min(i + 1, 12) } as CSSProperties}
              className={cn('stagger pressable rounded-[14px] border px-3.5 py-3 hover:-translate-y-0.5 hover:shadow-lift', e.is_hypothesis ? 'border-dashed border-[#f1a64d]' : 'border-line')}>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-sm font-bold">{EDGE_LABELS[e.label] ?? EDGE_LABELS[e.edge_type] ?? e.label}</span>
                {e.is_hypothesis && (
                  <span className="flex items-center gap-1 text-[11px] text-ink-2">
                    <KindBadge kind="hypothesis" /> {Math.round((e.confidence ?? 0) * 100)}%
                  </span>
                )}
              </div>
              <div className="text-sm">
                {String(cp?.okved_name ?? 'Контрагент вне базы')}
                <span className="ml-1.5 text-[13px] text-ink-2">{String(cp?.region ?? '')}</span>
              </div>
              <div className="text-[13px] text-ink-2">
                {e.amount_rub ? `${formatMoney(e.amount_rub)}` : ''}
                {e.share ? `${e.amount_rub ? ', ' : ''}${formatPercent(e.share)} оборота` : ''}
                {ev?.statement ? `${e.amount_rub || e.share ? '. ' : ''}${capitalizeFirst(String(ev.statement))}` : ''}
                {Array.isArray(ev?.basis) ? ` (основание: ${(ev!.basis as string[]).map(industryTag).join(', ')})` : ''}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
