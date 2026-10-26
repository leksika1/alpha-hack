// Карточка инсайта: шесть блоков кейса — что произошло, почему важно вам, последствия, что сделать, позиция банка,
// на чём основан вывод — плюс вопрос к аналитике, действие и отзыв.
import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button } from '@alfalab/core-components/button';
import { ArrowLeftMIcon } from '@alfalab/icons-glyph/ArrowLeftMIcon';
import { BookmarkMIcon } from '@alfalab/icons-glyph/BookmarkMIcon';
import { BookmarkLineMIcon } from '@alfalab/icons-glyph/BookmarkLineMIcon';
import { EyeOffMIcon } from '@alfalab/icons-glyph/EyeOffMIcon';
import { EyeMIcon } from '@alfalab/icons-glyph/EyeMIcon';
import { ExclamationCircleMIcon } from '@alfalab/icons-glyph/ExclamationCircleMIcon';
import { useBankProducts, useInsight, useInsightState, useTrace, useTrack } from '../../api/hooks';
import type { InsightBody, StatementKind } from '../../api/types';
import { useSession } from '../../auth/session';
import {
  ACTION_TYPES,
  cleanTitle,
  COVERAGE,
  EVENT_TYPES,
  featureLabel,
  formatDate,
  formatMoney,
  formatNumber,
  HORIZON,
  IMPACT_MEANING,
  isDemoStatement,
  PLAUSIBILITY,
  ROLE_LABELS,
  stripDemoPrefix,
} from '../../lib/format';
import { actionLabelClass, CardSkeleton, Disclaimers, ErrorBox, Flag, ImpactMeter, KindBadge, LinkButton, Note, Pill, PolarityStatus, UrgentStatus } from '../../components/ui';
import { SourceList, SourceRefs, SourcesProvider } from '../../components/insight/sources';
import { FeatureChip, RelevanceBreakdown, TracePanel } from '../../components/insight/TracePanel';
import { AskPanel } from '../../components/insight/AskPanel';
import { ActionModal, type ActionDraft } from '../../components/insight/ActionModal';
import { FeedbackPanel } from '../../components/insight/FeedbackPanel';
import { useToast } from '../../components/Toasts';
import { cn } from '../../lib/cn';

const KIND_ORDER: StatementKind[] = ['fact', 'bank_analytics', 'model_inference', 'hypothesis'];
const CTA_TYPES = new Set(['contact_manager', 'callback', 'application']);

const NAV = [
  ['what', 'Что произошло'],
  ['why', 'Почему важно вам'],
  ['consequences', 'Последствия'],
  ['actions', 'Что можно сделать'],
  ['bank', 'Позиция банка'],
  ['basis', 'На чём основано'],
  ['ask', 'Задать вопрос'],
] as const;

type Statement = InsightBody['statements'][number];

function Block({ id, num, title, children }: { id: string; num?: number; title: string; children: ReactNode }) {
  return (
    <section
      id={id}
      style={{ '--i': num ?? 7 } as CSSProperties}
      className="stagger card mt-3 scroll-mt-32.5 p-6"
    >
      <h2 className={`mb-3.5 flex items-center gap-2.5 text-xl ${title == 'Позиция Альфа-Банка' ? 'text-accent' : 'text-black'}`} >
        {num !== undefined && (
          <span className={`inline-flex size-7 shrink-0 items-center justify-center rounded-full text-sm text-white ${title == 'Позиция Альфа-Банка' ? 'bg-accent' : 'bg-night'}`}>
            {num}
          </span>
        )}
        {title}
      </h2>
      {children}
    </section >
  );
}

const SubTitle = ({ children }: { children: ReactNode }) => <h3 className="mt-5 mb-2.5 text-base">{children}</h3>;

const KIND_DOT: Record<StatementKind, string> = {
  fact: 'bg-[#b8b9c0]',
  bank_analytics: 'bg-accent',
  model_inference: 'bg-purple',
  hypothesis: 'bg-att-dot',
};

const KIND_GROUP: Record<StatementKind, { title: string; hint?: string }> = {
  fact: { title: 'Факты', hint: 'подтверждены источниками' },
  bank_analytics: { title: 'Аналитика Альфа-Банка', hint: 'позиция из материалов банка' },
  model_inference: { title: 'Выводы AI' },
  hypothesis: { title: 'Гипотезы' },
};

// Утверждение в рамке; тип задаёт заголовок группы, а не отдельная плашка.
function StatementItem({ text, refs, confidence }: { text: string; refs?: string[]; confidence?: number }) {
  return (
    <div className="rounded-xl border border-line px-3.5 py-2.5">
      <p className="mb-0">{stripDemoPrefix(text)}</p>
      {(!!refs?.length || confidence !== undefined) && (
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-ink-3">
          <SourceRefs ids={refs} />
          {confidence !== undefined && <span>уверенность {Math.round(confidence * 100)}%</span>}
        </div>
      )}
    </div>
  );
}

function StatementGroup({ kind, demo, count, children }: { kind: StatementKind; demo: boolean; count: number; children: ReactNode }) {
  const g = KIND_GROUP[kind];
  return (
    <div className="mt-5 first:mt-0">
      <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={cn('size-2 rounded-full', KIND_DOT[kind])} />
        <h3 className="text-[15px]">{demo && kind === 'bank_analytics' ? 'Материалы банка' : g.title}</h3>
        <span className="text-[13px] text-ink-3">{count}</span>
        <span className="ml-1 text-nowrap text-[13px] text-ink-2">{demo && kind === 'bank_analytics' ? 'демо-материал (синтетика), не аналитика Альфа-Банка' : g.hint}</span>
      </div>
      <div className="flex flex-col gap-2">{children}</div>
    </div>
  );
}

// Источник перехода для метрик воронки; API принимает только фиксированный набор значений.
const VIA = new Set(['feed', 'widget', 'digest', 'notification', 'search', 'other']);
const viaParam = (via: string | null) => (via && VIA.has(via) ? via : 'other');

export function InsightPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { companies, company, setCompanyId } = useSession();
  const positionParam = params.get('position');
  const insightQ = useInsight(id, viaParam(params.get('via')), positionParam ? Number(positionParam) : undefined);
  const traceQ = useTrace(id, !!insightQ.data);
  const products = useBankProducts();
  const state = useInsightState(id ?? '');
  const track = useTrack(id ?? '');
  const toast = useToast();
  const [draft, setDraft] = useState<ActionDraft | null>(null);

  const data = insightQ.data;
  const ins = data?.insight as unknown as InsightBody | undefined;

  // Инсайт мог прийти из уведомления по другой компании — переключаем контекст в шапке.
  useEffect(() => {
    if (data && company && data.company_id !== company.company_id && companies.some((c) => c.company_id === data.company_id)) {
      setCompanyId(data.company_id);
    }
  }, [data, company, companies, setCompanyId]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [id]);

  // Открытие инсайта = прочитан: бэкенд отмечает это отдельным действием open, после него лента и счётчики обновятся.
  const markedOpen = useRef<string | null>(null);
  useEffect(() => {
    if (data && !data.state.opened && markedOpen.current !== data.insight_id) {
      markedOpen.current = data.insight_id;
      state.mutate('open');
    }
  }, [data, state]);

  const featureMap = useMemo(
    () => (traceQ.data?.company_features ?? {}) as Record<string, { value: unknown; source: string; note?: string | null }>,
    [traceQ.data],
  );
  const statementsById = useMemo(() => Object.fromEntries((ins?.statements ?? []).map((s) => [s.id, s])), [ins]);

  if (insightQ.isLoading)
    return (
      <div>
        <CardSkeleton rows={5} />
        <CardSkeleton rows={4} />
      </div>
    );
  if (insightQ.isError || !data || !ins) return <ErrorBox error={insightQ.error} onRetry={() => insightQ.refetch()} />;

  const st = data.state;
  const demo = (s: Statement) => isDemoStatement(s, ins.sources);
  const refBadges = (refs: string[]) => refs.map((sid) => statementsById[sid] && <KindBadge key={sid} kind={statementsById[sid].kind} demo={demo(statementsById[sid])} />);
  const productName = (pid: string | null) => products.data?.find((p) => p.product_id === pid)?.name;
  const firstWhy = ins.why_it_matters[0]?.text ?? '';
  const ctaActions = ins.actions.filter((a) => CTA_TYPES.has(a.type));
  const otherActions = ins.actions.filter((a) => !CTA_TYPES.has(a.type));
  const openDraft = (type: string, productId: string | null) => setDraft({ type: type as ActionDraft['type'], productId, reason: firstWhy });

  const doState = (action: 'hide' | 'unhide') =>
    state.mutate(action, {
      onSuccess: () => toast.show({ title: action === 'hide' ? 'Инсайт скрыт из ленты' : 'Инсайт возвращён в ленту', tone: 'positive', ttl: 3500 }),
    });

  const toggleTrack = () =>
    track.mutate(!st.saved, {
      onSuccess: () =>
        toast.show(
          st.saved
            ? { title: 'Больше не отслеживается', ttl: 3500 }
            : { title: 'Отслеживается', body: 'Инсайт в фильтре «Отслеживаемые», об обновлениях события сообщим.', tone: 'positive', ttl: 4500 },
        ),
    });

  const suggestions = ['Как это повлияет на наши финансы в ближайший квартал?', 'А если смотреть на другое юрлицо нашей группы?', 'Что об этом пишет аналитика банка?'];

  return (
    <SourcesProvider insightId={data.insight_id} sources={ins.sources}>
      <div>
        <div className="mt-1 mb-4">
          <LinkButton onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/analytics'))}>
            <ArrowLeftMIcon width={20} height={20} /> К ленте
          </LinkButton>
        </div>

        <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <div className="min-w-0">
            <header className="card p-6">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                {ins.urgent && <UrgentStatus />}
                <PolarityStatus polarity={ins.polarity} />
                <span className="text-ink-2">
                  {EVENT_TYPES[ins.event.event_type] ?? ins.event.event_type}, {formatDate(ins.event.occurred_at, true)}
                </span>
                {st.updated && <Flag tone="orange">Обновлено</Flag>}
              </div>
              <h1 className="mt-3 mb-2.5 text-[26px] leading-tight">{cleanTitle(ins.title)}</h1>
              <p className="text-base">{ins.summary}</p>
              {!data.role_match && (
                <Note>
                  Инсайт подготовлен для роли «{ROLE_LABELS[ins.role_variant]}». Вариант под вашу текущую роль генерируется — порядок ленты уже учитывает смену
                  роли.
                </Note>
              )}
              <div className="mt-4 flex flex-wrap gap-2">
                <Button view="secondary" size={40} loading={track.isPending} leftAddons={st.saved ? <BookmarkMIcon /> : <BookmarkLineMIcon />} onClick={toggleTrack}>
                  {st.saved ? 'Отслеживается' : 'Отслеживать'}
                </Button>
                <Button view="secondary" size={40} leftAddons={st.hidden ? <EyeMIcon /> : <EyeOffMIcon />} onClick={() => doState(st.hidden ? 'unhide' : 'hide')}>
                  {st.hidden ? 'Вернуть в ленту' : 'Скрыть'}
                </Button>
              </div>
            </header>

            <nav className="sticky top-[72px] z-5 my-3 flex gap-1.5 overflow-x-auto bg-page py-2 [scrollbar-width:none]" aria-label="Разделы инсайта">
              {NAV.map(([key, label]) => (
                <a
                  key={key}
                  href={`#${key}`}
                  className="rounded-[14px] bg-white px-3 py-1.5 text-[13px] whitespace-nowrap text-ink shadow-card hover:bg-muted-2 hover:no-underline"
                  onClick={(e) => {
                    e.preventDefault();
                    document.getElementById(key)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                  }}
                >
                  {label}
                </a>
              ))}
            </nav>

            <Block id="what" num={1} title="Что произошло">
              <h3 className="mb-1.5 text-[17px]">{ins.event.title}</h3>
              <div className="mt-3 flex flex-col gap-3">
                {ins.event.key_facts.map((f, i) => (
                  <StatementItem key={i} text={f.text} refs={f.source_refs} />
                ))}
              </div>
              {ins.event.participants?.length > 0 && <p className="text-[13px] text-ink-2">Участники: {ins.event.participants.map((p) => p.name).join(', ')}</p>}
            </Block>

            <Block id="why" num={2} title="Почему это важно именно вашей компании">
              {ins.why_it_matters.map((w, i) => (
                <div key={i} className="border-b border-line py-3 last-of-type:border-b-0">
                  <p className="text-base font-medium">{w.text}</p>
                  <div className="my-1.5 flex flex-wrap gap-1.5">
                    {w.feature_refs.map((path) => (
                      <FeatureChip key={path} path={path} f={featureMap[path]} />
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {w.statement_refs.map((sid) => {
                      const s = statementsById[sid];
                      return s && s.kind !== 'fact' ? <KindBadge key={sid} kind={s.kind} demo={demo(s)} /> : null;
                    })}
                  </div>
                </div>
              ))}
              <p className="text-[13px] text-ink-2">
                Почему инсайт выше в ленте: совпадение по признакам {ins.relevance.matched_features.map(featureLabel).join(', ').toLowerCase()}.{' '}
                <Link to="/analytics/profile">Проверить или дополнить профиль</Link>
              </p>
            </Block>

            <Block id="consequences" num={3} title="Возможные последствия">
              <div className="mb-4 rounded-xl bg-muted px-3.5 py-3">
                <ImpactMeter level={ins.impact.level} score={ins.impact.score} />
                <p className="mt-1.5 text-[13px] text-ink-2">
                  {IMPACT_MEANING[ins.impact.level]}. {ins.impact.rationale}
                </p>
              </div>
              {ins.impact.quantitative?.length > 0 && (
                <div className="mb-4 flex flex-wrap gap-3">
                  {ins.impact.quantitative.map((q, i) => (
                    <div key={i} className="min-w-[220px] flex-1 rounded-xl border border-line px-3.5 py-3">
                      <div className="text-[22px] font-bold">
                        {q.unit.includes('₽') ? formatMoney(q.value) : formatNumber(q.value)}
                        {' '}<span className="ml-1 text-[13px] font-normal text-ink-2">{q.unit.replace(/^₽/, '').trim()}</span>
                      </div>
                      <div className="text-[13px] text-ink-2">{q.period}</div>
                      <div className="text-[13px] text-ink-2">{q.basis}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="grid gap-3 ">
                <ConsequenceColumn title="Риски" tone="risk" empty="Существенных рисков не выявлено." items={ins.consequences.risks} refBadges={refBadges} />
                {ins.consequences.opportunities[0] && <ConsequenceColumn title="Возможности" tone="opp" empty="Явных возможностей не выявлено." items={ins.consequences.opportunities} refBadges={refBadges} />}
              </div>
              {ins.consequences.scenarios.length > 0 && (
                <>
                  <SubTitle>Сценарии</SubTitle>
                  <div className="flex w-full flex-col gap-3 md:flex-row">
                    {ins.consequences.scenarios.map((s, i) => (
                      <div key={i} className={cn('flex-1 rounded-xl border p-4', s.plausibility === 'high' ? 'border-[#c9ccd3]' : 'border-line')}>
                        <div className="mb-1.5 flex justify-between gap-2">
                          <b>{s.name}</b>
                          <span className="text-xs whitespace-nowrap text-ink-2">{PLAUSIBILITY[s.plausibility] ?? s.plausibility}</span>
                        </div>
                        <p>{s.logic}</p>
                        {s.signals.length > 0 && <p className="text-[13px] text-ink-2">За чем следить: {s.signals.join(', ')}</p>}
                        <div className="mt-1 flex flex-wrap gap-1.5">{refBadges(s.statement_refs)}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </Block>

            <Block id="actions" num={4} title="Что можно сделать">
              <ul className="divide-y divide-line">
                {ins.actions.map((a, i) => (
                  <li key={i} className="flex items-center gap-4 py-3.5 first:pt-0 last:pb-0">
                    <div className="min-w-0 flex-1">
                      <div className={actionLabelClass(a.type)}>{ACTION_TYPES[a.type] ?? a.type}</div>
                      <div>{a.text}</div>
                      {a.product_id && productName(a.product_id) && <div className="text-[13px] text-ink-2">Продукт: {productName(a.product_id)}</div>}
                    </div>
                    {CTA_TYPES.has(a.type) && (
                      <Button view="primary" size={32} onClick={() => openDraft(a.type, a.product_id)}>
                        Оформить
                      </Button>
                    )}
                    {a.type === 'monitor' && (
                      <Button view="secondary" size={32} disabled={st.saved} onClick={toggleTrack}>
                        {st.saved ? 'Отслеживается' : 'Отслеживать'}
                      </Button>
                    )}
                    {a.type === 'read_source' && a.source_ref && <SourceRefs ids={[a.source_ref]} />}
                  </li>
                ))}
              </ul>
              {ctaActions.length === 0 && otherActions.length > 0 && (
                <p className="text-[13px] text-ink-2">Банковский продукт здесь не предлагаем: действие не требует участия банка.</p>
              )}
            </Block>

            <Block id="bank" num={5} title="Позиция Альфа-Банка">
              <p className="text-ink-2">{COVERAGE[ins.bank_view.coverage] ?? ins.bank_view.coverage}</p>
              {ins.bank_view.model_disagrees_with_bank && (
                <Note tone="warning">
                  <ExclamationCircleMIcon width={20} height={20} />
                  <span>
                    <b>Вывод модели расходится с позицией банка.</b> {ins.bank_view.disagreement_note} Это вывод AI, а не официальная позиция Альфа-Банка.
                  </span>
                </Note>
              )}
              {ins.bank_view.positions.map((p, i) => (
                <figure key={i} className="my-3 rounded-xl border border-line px-4 py-3.5">
                  <p className="font-medium">{p.text}</p>
                  {p.quote && <blockquote className="my-2 text-ink-2 italic">«{p.quote}»</blockquote>}
                  <figcaption>
                    <SourceRefs ids={[p.source_ref]} />
                  </figcaption>
                </figure>
              ))}
              {ins.bank_view.positions.length === 0 && (
                <p className="text-[13px] text-ink-2">Материалов аналитики банка по этому событию нет — выводы ниже сделаны без опоры на них.</p>
              )}
            </Block>

            <Block id="basis" num={6} title="На чём основан вывод">
              {KIND_ORDER.map((kind) => {
                const list = ins.statements.filter((s) => s.kind === kind);
                if (!list.length) return null;
                return (
                  <StatementGroup key={kind} kind={kind} count={list.length} demo={list.every(demo)}>
                    {list.map((s) => (
                      <StatementItem key={s.id} text={s.text} refs={s.source_refs} confidence={s.confidence} />
                    ))}
                  </StatementGroup>
                );
              })}
              <SubTitle>Источники</SubTitle>
              <SourceList sources={ins.sources} />
              <RelevanceBreakdown breakdown={ins.relevance.breakdown} score={ins.relevance.score} />
              {traceQ.data && <TracePanel trace={traceQ.data} />}
              <p className="text-[13px] text-ink-2">
                Модели: {ins.models_used.map((m) => `${m.name} (${m.purpose})`).join(', ')}. Сформировано {formatDate(ins.generated_at, true)}, актуально до{' '}
                {formatDate(ins.expires_at)}
              </p>
            </Block>

            <Block id="ask" title="Уточнить сценарий">
              <AskPanel insightId={data.insight_id} companyId={data.company_id} suggestions={suggestions} />
            </Block>

            <Disclaimers items={ins.disclaimers} />
          </div>

          <aside className="flex flex-col gap-3 xl:sticky xl:top-22">
            <div className="card flex flex-col gap-2.5 p-6">
              <div className="label-caps mt-1">Влияние на компанию</div>
              <ImpactMeter level={ins.impact.level} score={ins.impact.score} />
              <div className="text-sm text-ink-2">{firstWhy}</div>
              <div className="label-caps mt-1">Следующий шаг</div>
              {ctaActions.slice(0, 1).map((a, i) => (
                <Button key={i} view="accent" size={48} block onClick={() => openDraft(a.type, a.product_id)}>
                  {ACTION_TYPES[a.type]}
                </Button>
              ))}
              <Button view="secondary" size={48} block onClick={() => openDraft('callback', null)}>
                Заказать звонок
              </Button>
              <Button view="secondary" size={48} block onClick={() => document.getElementById('ask')?.scrollIntoView({ behavior: 'smooth' })}>
                Задать вопрос
              </Button>
            </div>
            <div className="card p-6">
              <FeedbackPanel insightId={data.insight_id} hidden={st.hidden} />
            </div>
          </aside>
        </div>

        <ActionModal insightId={data.insight_id} draft={draft} onClose={() => setDraft(null)} />
      </div>
    </SourcesProvider>
  );
}

function ConsequenceColumn({
  title,
  tone,
  empty,
  items,
  refBadges,
}: {
  title: string;
  tone: 'risk' | 'opp';
  empty: string;
  items: InsightBody['consequences']['risks'];
  refBadges: (refs: string[]) => ReactNode;
}) {
  return (
    <div className={cn('rounded-xl px-4 py-3.5', tone === 'risk' ? 'bg-neg-soft' : 'bg-[#f1fbf3]')}>
      <h3 className={cn('mb-2 text-[15px]', tone === 'risk' ? 'text-neg' : 'text-pos')}>{title}</h3>
      {items.length === 0 && <p className="text-[13px] text-ink-2">{empty}</p>}
      {items.map((c, i) => (
        <div key={i} className="border-black/6 [&+&]:mt-3 [&+&]:border-t [&+&]:pt-3">
          <p>{c.text}</p>
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            {c.horizon && <Pill>Горизонт: {HORIZON[c.horizon] ?? c.horizon}</Pill>}
            {refBadges(c.statement_refs)}
          </div>
          {c.drivers.length > 0 && <p className="text-[13px] text-ink-2">Что повлияет: {c.drivers.join(', ')}</p>}
        </div>
      ))}
    </div>
  );
}
