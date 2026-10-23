// Карточка инсайта: шесть блоков кейса — что произошло, почему важно вам, последствия, что сделать, позиция банка,
// на чём основан вывод — плюс вопрос к аналитике, действие и отзыв.
import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button } from '@alfalab/core-components/button';
import { ArrowLeftMIcon } from '@alfalab/icons-glyph/ArrowLeftMIcon';
import { BookmarkMIcon } from '@alfalab/icons-glyph/BookmarkMIcon';
import { BookmarkLineMIcon } from '@alfalab/icons-glyph/BookmarkLineMIcon';
import { EyeOffMIcon } from '@alfalab/icons-glyph/EyeOffMIcon';
import { EyeMIcon } from '@alfalab/icons-glyph/EyeMIcon';
import { TelescopeMIcon } from '@alfalab/icons-glyph/TelescopeMIcon';
import { ExclamationCircleMIcon } from '@alfalab/icons-glyph/ExclamationCircleMIcon';
import { useBankProducts, useInsight, useInsightState, useTrace } from '../../api/hooks';
import type { InsightBody, StatementKind } from '../../api/types';
import { useSession } from '../../auth/session';
import {
  ACTION_TYPES,
  COVERAGE,
  EVENT_TYPES,
  featureLabel,
  featureValue,
  FEATURE_SOURCE,
  formatDate,
  formatMoney,
  formatNumber,
  HORIZON,
  isDemoStatement,
  IMPACT_MEANING,
  PLAUSIBILITY,
  ROLE_LABELS,
  STATEMENT_KIND,
} from '../../lib/format';
import { CardSkeleton, Disclaimers, ErrorBox, ImpactMeter, KindBadge, PolarityStatus, UrgentStatus } from '../../components/ui';
import { SourceList, SourceRefs, SourcesProvider } from '../../components/insight/sources';
import { RelevanceBreakdown, TracePanel } from '../../components/insight/TracePanel';
import { AskPanel } from '../../components/insight/AskPanel';
import { ActionModal, type ActionDraft } from '../../components/insight/ActionModal';
import { FeedbackPanel } from '../../components/insight/FeedbackPanel';
import { useToast } from '../../components/Toasts';

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

export function InsightPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { companies, company, setCompanyId } = useSession();
  const positionParam = params.get('position');
  const insightQ = useInsight(id, params.get('via') ?? 'direct', positionParam ? Number(positionParam) : undefined);
  const traceQ = useTrace(id, !!insightQ.data);
  const products = useBankProducts();
  const state = useInsightState(id ?? '');
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

  const featureMap = useMemo(
    () => (traceQ.data?.company_features ?? {}) as Record<string, { value: unknown; source: string; note?: string | null }>,
    [traceQ.data],
  );
  const statementsById = useMemo(() => Object.fromEntries((ins?.statements ?? []).map((s) => [s.id, s])), [ins]);

  if (insightQ.isLoading) return <div className="insight-page"><CardSkeleton rows={5} /><CardSkeleton rows={4} /></div>;
  if (insightQ.isError || !data || !ins) return <ErrorBox error={insightQ.error} onRetry={() => insightQ.refetch()} />;

  const st = data.state;
  const productName = (pid: string | null) => products.data?.find((p) => p.product_id === pid)?.name;
  const firstWhy = ins.why_it_matters[0]?.text ?? '';
  const ctaActions = ins.actions.filter((a) => CTA_TYPES.has(a.type));
  const otherActions = ins.actions.filter((a) => !CTA_TYPES.has(a.type));

  const doState = (action: 'save' | 'unsave' | 'hide' | 'unhide' | 'watch') =>
    state.mutate(action, {
      onSuccess: () => {
        const msg: Record<string, string> = {
          save: 'Инсайт сохранён',
          unsave: 'Убрано из сохранённых',
          hide: 'Инсайт скрыт из ленты',
          unhide: 'Инсайт возвращён в ленту',
          watch: 'Вы наблюдаете за событием — сообщим об обновлениях',
        };
        toast.show({ title: msg[action], tone: 'positive', ttl: 3500 });
      },
    });

  const suggestions = [
    'Как это повлияет на наши финансы в ближайший квартал?',
    'А если смотреть на другое юрлицо нашей группы?',
    'Что об этом пишет аналитика банка?',
  ];

  return (
    <SourcesProvider insightId={data.insight_id} sources={ins.sources}>
      <div className="insight-page">
        <div className="insight-page__back">
          <button className="link-btn" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/analytics'))}>
            <ArrowLeftMIcon width={20} height={20} /> К ленте
          </button>
        </div>

        <div className="insight-layout">
          <div className="insight-main">
            <header className="card card--pad insight-hero">
              <div className="insight-hero__statuses">
                {ins.urgent && <UrgentStatus />}
                <PolarityStatus polarity={ins.polarity} />
                <span className="muted">
                  {EVENT_TYPES[ins.event.event_type] ?? ins.event.event_type} · {formatDate(ins.event.occurred_at, true)}
                </span>
                {st.updated && <span className="insight-card__updated">Обновлено</span>}
              </div>
              <h1 className="insight-hero__title">{ins.title}</h1>
              <p className="insight-hero__summary">{ins.summary}</p>
              {!data.role_match && (
                <div className="note note--info">
                  Инсайт подготовлен для роли «{ROLE_LABELS[ins.role_variant]}». Вариант под вашу текущую роль генерируется — порядок ленты уже учитывает
                  смену роли.
                </div>
              )}
              <div className="insight-hero__actions">
                <Button
                  view="secondary"
                  size={40}
                  leftAddons={st.saved ? <BookmarkMIcon /> : <BookmarkLineMIcon />}
                  onClick={() => doState(st.saved ? 'unsave' : 'save')}
                >
                  {st.saved ? 'Сохранено' : 'Сохранить'}
                </Button>
                <Button view="secondary" size={40} leftAddons={<TelescopeMIcon />} onClick={() => doState('watch')}>
                  Наблюдать
                </Button>
                <Button
                  view="secondary"
                  size={40}
                  leftAddons={st.hidden ? <EyeMIcon /> : <EyeOffMIcon />}
                  onClick={() => doState(st.hidden ? 'unhide' : 'hide')}
                >
                  {st.hidden ? 'Вернуть в ленту' : 'Скрыть'}
                </Button>
              </div>
            </header>

            <nav className="anchor-nav" aria-label="Разделы инсайта">
              {NAV.map(([key, label]) => (
                <a key={key} href={`#${key}`} onClick={(e) => { e.preventDefault(); document.getElementById(key)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>
                  {label}
                </a>
              ))}
            </nav>

            {/* 1. Что произошло */}
            <section className="card card--pad block" id="what">
              <h2 className="block__title">
                <span className="block__num">1</span> Что произошло
              </h2>
              <h3 className="block__event">{ins.event.title}</h3>
              <p>{ins.event.summary}</p>
              <ul className="facts">
                {ins.event.key_facts.map((f, i) => (
                  <li key={i} className="statement">
                    <KindBadge kind="fact" />
                    <span>
                      {f.text} <SourceRefs ids={f.source_refs} />
                    </span>
                  </li>
                ))}
              </ul>
              {ins.event.participants?.length > 0 && (
                <p className="muted small">Участники: {ins.event.participants.map((p) => p.name).join(', ')}</p>
              )}
            </section>

            {/* 2. Почему это важно вам */}
            <section className="card card--pad block" id="why">
              <h2 className="block__title">
                <span className="block__num">2</span> Почему это важно именно вашей компании
              </h2>
              {ins.why_it_matters.map((w, i) => (
                <div key={i} className="why-item">
                  <p className="why-item__text">{w.text}</p>
                  <div className="feature-chips">
                    {w.feature_refs.map((path) => {
                      const f = featureMap[path];
                      return (
                        <span key={path} className="feature-chip" title={f?.note ?? undefined}>
                          {featureLabel(path)}
                          {f && (
                            <>
                              : <b>{featureValue(path, f.value)}</b>
                              <span className="feature-chip__src">{FEATURE_SOURCE[f.source] ?? f.source}</span>
                            </>
                          )}
                        </span>
                      );
                    })}
                  </div>
                  {w.statement_refs.map((sid) => {
                    const s = statementsById[sid];
                    return s && s.kind !== 'fact' ? (
                      <div key={sid} className="why-item__kind">
                        <KindBadge kind={s.kind} demo={isDemoStatement(s, ins.sources)} />
                      </div>
                    ) : null;
                  })}
                </div>
              ))}
              <p className="muted small">
                Почему инсайт выше в ленте: совпадение по признакам {ins.relevance.matched_features.map(featureLabel).join(', ').toLowerCase()}.{' '}
                <Link to="/analytics/profile">Проверить или дополнить профиль</Link>
              </p>
            </section>

            {/* 3. Последствия */}
            <section className="card card--pad block" id="consequences">
              <h2 className="block__title">
                <span className="block__num">3</span> Возможные последствия
              </h2>
              <div className="impact-box">
                <ImpactMeter level={ins.impact.level} score={ins.impact.score} />
                <p className="muted small">
                  {IMPACT_MEANING[ins.impact.level]}. {ins.impact.rationale}
                </p>
              </div>
              {ins.impact.quantitative?.length > 0 && (
                <div className="quant">
                  {ins.impact.quantitative.map((q, i) => (
                    <div key={i} className="quant__item">
                      <div className="quant__value">
                        {q.unit.includes('₽') ? formatMoney(q.value) : formatNumber(q.value)}
                        <span className="quant__unit">{q.unit.replace(/^₽/, '').trim()}</span>
                      </div>
                      <div className="quant__period">{q.period}</div>
                      <div className="muted small">{q.basis}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="conseq">
                <div className="conseq__col conseq__col--risk">
                  <h3>Риски</h3>
                  {ins.consequences.risks.length === 0 && <p className="muted small">Существенных рисков не выявлено.</p>}
                  {ins.consequences.risks.map((r, i) => (
                    <ConsequenceItem key={i} c={r} statementsById={statementsById} sources={ins.sources} />
                  ))}
                </div>
                <div className="conseq__col conseq__col--opp">
                  <h3>Возможности</h3>
                  {ins.consequences.opportunities.length === 0 && <p className="muted small">Явных возможностей не выявлено.</p>}
                  {ins.consequences.opportunities.map((r, i) => (
                    <ConsequenceItem key={i} c={r} statementsById={statementsById} sources={ins.sources} />
                  ))}
                </div>
              </div>
              {ins.consequences.scenarios.length > 0 && (
                <>
                  <h3 className="block__sub">Сценарии</h3>
                  <div className="scenarios">
                    {ins.consequences.scenarios.map((s, i) => (
                      <div key={i} className={`scenario scenario--${s.plausibility}`}>
                        <div className="scenario__head">
                          <b>{s.name}</b>
                          <span className="scenario__pl">{PLAUSIBILITY[s.plausibility] ?? s.plausibility}</span>
                        </div>
                        <p>{s.logic}</p>
                        {s.signals.length > 0 && <p className="muted small">За чем следить: {s.signals.join(', ')}</p>}
                        {s.statement_refs.map(
                          (sid) =>
                            statementsById[sid] && <KindBadge key={sid} kind={statementsById[sid].kind} demo={isDemoStatement(statementsById[sid], ins.sources)} />,
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </section>

            {/* 4. Что можно сделать */}
            <section className="card card--pad block" id="actions">
              <h2 className="block__title">
                <span className="block__num">4</span> Что можно сделать
              </h2>
              <ul className="actions-list">
                {ins.actions.map((a, i) => (
                  <li key={i} className="actions-list__item">
                    <span className={`actions-list__type actions-list__type--${a.type}`}>{ACTION_TYPES[a.type] ?? a.type}</span>
                    <div className="actions-list__body">
                      <span>{a.text}</span>
                      {a.product_id && productName(a.product_id) && <span className="muted small">Продукт: {productName(a.product_id)}</span>}
                    </div>
                    {CTA_TYPES.has(a.type) && (
                      <Button view="primary" size={32} onClick={() => setDraft({ type: a.type as ActionDraft['type'], productId: a.product_id, reason: firstWhy })}>
                        Оформить
                      </Button>
                    )}
                    {a.type === 'monitor' && (
                      <Button view="secondary" size={32} onClick={() => doState('watch')}>
                        Наблюдать
                      </Button>
                    )}
                    {a.type === 'read_source' && a.source_ref && <SourceRefs ids={[a.source_ref]} />}
                  </li>
                ))}
              </ul>
              {ctaActions.length === 0 && otherActions.length > 0 && (
                <p className="muted small">Банковский продукт здесь не предлагаем: действие не требует участия банка.</p>
              )}
            </section>

            {/* 5. Позиция банка */}
            <section className="card card--pad block block--bank" id="bank">
              <h2 className="block__title">
                <span className="block__num block__num--bank">5</span> Позиция Альфа-Банка
              </h2>
              <p className="muted">{COVERAGE[ins.bank_view.coverage] ?? ins.bank_view.coverage}</p>
              {ins.bank_view.model_disagrees_with_bank && (
                <div className="note note--warning">
                  <ExclamationCircleMIcon width={20} height={20} />
                  <span>
                    <b>Вывод модели расходится с позицией банка.</b> {ins.bank_view.disagreement_note} Это вывод AI, а не официальная позиция
                    Альфа-Банка.
                  </span>
                </div>
              )}
              {ins.bank_view.positions.map((p, i) => (
                <figure key={i} className="bank-quote">
                  <p className="bank-quote__text">{p.text}</p>
                  {p.quote && <blockquote>«{p.quote}»</blockquote>}
                  <figcaption>
                    <SourceRefs ids={[p.source_ref]} />
                  </figcaption>
                </figure>
              ))}
              {ins.bank_view.positions.length === 0 && <p className="muted small">Материалов аналитики банка по этому событию нет — выводы ниже сделаны без опоры на них.</p>}
            </section>

            {/* 6. На чём основан вывод */}
            <section className="card card--pad block" id="basis">
              <h2 className="block__title">
                <span className="block__num">6</span> На чём основан вывод
              </h2>
              <div className="kinds-legend">
                {KIND_ORDER.map((k) => (
                  <span key={k}>
                    <KindBadge kind={k} /> <span className="muted small">{STATEMENT_KIND[k].hint}</span>
                  </span>
                ))}
              </div>
              {KIND_ORDER.map((kind) => {
                const list = ins.statements.filter((s) => s.kind === kind);
                if (!list.length) return null;
                return (
                  <div key={kind} className="statements-group">
                    {list.map((s) => (
                      <div key={s.id} className="statement">
                        <KindBadge kind={s.kind} demo={isDemoStatement(s, ins.sources)} />
                        <span>
                          {s.text} <SourceRefs ids={s.source_refs} />
                          <span className="statement__conf" title="Уверенность">
                            {Math.round(s.confidence * 100)}%
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                );
              })}
              <h3 className="block__sub">Источники</h3>
              <SourceList sources={ins.sources} />
              <RelevanceBreakdown breakdown={ins.relevance.breakdown} score={ins.relevance.score} />
              {traceQ.data && <TracePanel trace={traceQ.data} />}
              <p className="muted small">
                Модели: {ins.models_used.map((m) => `${m.name} (${m.purpose})`).join(', ')} · сформировано {formatDate(ins.generated_at, true)} · актуально до{' '}
                {formatDate(ins.expires_at)}
              </p>
            </section>

            {/* Вопрос к аналитике */}
            <section className="card card--pad block" id="ask">
              <h2 className="block__title">Уточнить сценарий</h2>
              <AskPanel insightId={data.insight_id} companyId={data.company_id} suggestions={suggestions} />
            </section>

            <Disclaimers items={ins.disclaimers} />
          </div>

          <aside className="insight-aside">
            <div className="card card--pad aside-card">
              <div className="aside-card__label">Влияние на компанию</div>
              <ImpactMeter level={ins.impact.level} score={ins.impact.score} />
              <div className="aside-card__why">{firstWhy}</div>
              <div className="aside-card__label">Следующий шаг</div>
              {ctaActions.slice(0, 1).map((a, i) => (
                <Button key={i} view="accent" size={48} block onClick={() => setDraft({ type: a.type as ActionDraft['type'], productId: a.product_id, reason: firstWhy })}>
                  {ACTION_TYPES[a.type]}
                </Button>
              ))}
              <Button
                view="secondary"
                size={48}
                block
                onClick={() => setDraft({ type: 'callback', productId: null, reason: firstWhy })}
              >
                Заказать звонок
              </Button>
              <Button view="secondary" size={48} block onClick={() => document.getElementById('ask')?.scrollIntoView({ behavior: 'smooth' })}>
                Задать вопрос
              </Button>
            </div>
            <div className="card card--pad aside-card">
              <FeedbackPanel insightId={data.insight_id} hidden={st.hidden} />
            </div>
          </aside>
        </div>

        <ActionModal insightId={data.insight_id} draft={draft} onClose={() => setDraft(null)} />
      </div>
    </SourcesProvider>
  );
}

function ConsequenceItem({
  c,
  statementsById,
  sources,
}: {
  c: InsightBody['consequences']['risks'][number];
  statementsById: Record<string, InsightBody['statements'][number]>;
  sources: InsightBody['sources'];
}) {
  return (
    <div className="conseq__item">
      <p>{c.text}</p>
      <div className="conseq__meta">
        {c.horizon && <span className="pill pill--grey">Горизонт: {HORIZON[c.horizon] ?? c.horizon}</span>}
        {c.statement_refs.map(
          (sid) => statementsById[sid] && <KindBadge key={sid} kind={statementsById[sid].kind} demo={isDemoStatement(statementsById[sid], sources)} />,
        )}
      </div>
      {c.drivers.length > 0 && <p className="muted small">Что повлияет: {c.drivers.join(', ')}</p>}
    </div>
  );
}
