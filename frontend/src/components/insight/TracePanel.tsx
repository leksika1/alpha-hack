// Трасса (CJM 05): какие шаги сделал агент, какие инструменты и признаки использовал, что проверил валидатор.
import { useState } from 'react';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';
import type { Trace } from '../../api/types';
import { featureLabel, featureValue, FEATURE_SOURCE, formatNumber } from '../../lib/format';

const STEP_TYPES: Record<string, string> = {
  plan: 'План',
  tool_call: 'Инструмент',
  retrieval: 'Поиск',
  llm_call: 'Вызов LLM',
  critique: 'Самопроверка',
  self_check: 'Самопроверка',
  retry: 'Повтор',
  final: 'Итог',
  validate: 'Проверка',
};

const COMPONENTS: Record<string, string> = {
  industry: 'Отрасль',
  counterparty: 'Контрагенты',
  fx_country: 'Страны ВЭД и валюта',
  rate: 'Ставка и кредиты',
  commodity: 'Сырьё',
  region: 'Регион',
  group: 'Группа компаний',
  user_provided: 'Данные от вас',
  bank_analytics: 'Аналитика банка',
  recency: 'Свежесть',
  sanctions: 'Санкции',
  regulation_tax: 'Регулирование',
  deposits: 'Депозиты',
};

type Step = {
  seq: number;
  step_type: string;
  name: string;
  status: string;
  input_preview: string | null;
  output_preview: string | null;
  model: string | null;
  latency_ms: number | null;
  error: string | null;
  attempt: number;
};

export function RelevanceBreakdown({ breakdown, score }: { breakdown: Record<string, number>; score: number }) {
  const entries = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, v]) => v), 0.0001);
  return (
    <div className="breakdown">
      <div className="breakdown__head">
        Релевантность для компании: <b>{formatNumber(score * 100)}%</b>
      </div>
      {entries.map(([k, v]) => (
        <div key={k} className="breakdown__row">
          <span className="breakdown__label">{COMPONENTS[k] ?? k}</span>
          <span className="breakdown__bar">
            <span style={{ width: `${(v / max) * 100}%` }} />
          </span>
          <span className="breakdown__value">+{formatNumber(v * 100)}</span>
        </div>
      ))}
    </div>
  );
}

export function TracePanel({ trace }: { trace: Trace }) {
  const [open, setOpen] = useState(false);
  const steps = (trace.steps ?? []) as unknown as Step[];
  const run = trace.run as Record<string, unknown> | null;
  const usage = (run?.usage ?? {}) as Record<string, number>;
  const validation = trace.validation as Record<string, string>;
  const llmCalls = (trace.llm_calls ?? []) as Record<string, unknown>[];
  const features = Object.entries(trace.company_features ?? {}) as [string, { value: unknown; source: string; note?: string | null }][];

  return (
    <div className={`trace ${open ? 'is-open' : ''}`}>
      <button className="trace__toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>
          <b>Трасса анализа</b>
          <span className="muted">
            {' '}
            · {steps.length} шагов агента · {llmCalls.length} вызовов LLM · проверка: {validation?.status === 'passed' ? 'пройдена' : validation?.status ?? '—'}
          </span>
        </span>
        <ChevronDownMIcon className="trace__chevron" />
      </button>
      {open && (
        <div className="trace__body">
          <div className="trace__grid">
            <div>
              <div className="trace__label">Запуск</div>
              <div className="trace__mono">{trace.trace_id}</div>
              <div className="muted small">
                {String(run?.generator ?? '')} · {String(run?.outcome ?? run?.status ?? '')}
                {usage.latency_ms !== undefined && ` · ${usage.latency_ms} мс`}
                {usage.tokens_in ? ` · ${usage.tokens_in}/${usage.tokens_out} токенов` : ''}
              </div>
            </div>
            <div>
              <div className="trace__label">Проверка валидатором</div>
              <div className={validation?.status === 'passed' ? 'text-positive' : 'text-negative'}>
                {validation?.status === 'passed' ? 'Контракт и бизнес-правила соблюдены' : validation?.status}
              </div>
              <div className="muted small">{validation?.rules}</div>
            </div>
          </div>

          {features.length > 0 && (
            <>
              <div className="trace__label">Признаки компании, на которые опирается вывод</div>
              <div className="feature-chips">
                {features.map(([path, f]) => (
                  <span key={path} className="feature-chip" title={f.note ?? undefined}>
                    {featureLabel(path)}: <b>{featureValue(path, f.value)}</b>
                    <span className="feature-chip__src">{FEATURE_SOURCE[f.source] ?? f.source}</span>
                  </span>
                ))}
              </div>
            </>
          )}

          <div className="trace__label">Шаги агента</div>
          <ol className="steps">
            {steps.map((s) => (
              <li key={s.seq} className={`steps__item steps__item--${s.status}`}>
                <span className="steps__type">{STEP_TYPES[s.step_type] ?? s.step_type}</span>
                <span className="steps__name">
                  {s.name}
                  {s.attempt > 1 && <span className="steps__retry"> · попытка {s.attempt}</span>}
                </span>
                {s.output_preview && <span className="steps__out">→ {s.output_preview}</span>}
                {s.error && <span className="steps__err">{s.error}</span>}
                {s.latency_ms != null && <span className="steps__ms">{s.latency_ms} мс</span>}
              </li>
            ))}
          </ol>

          {llmCalls.length > 0 && (
            <>
              <div className="trace__label">Вызовы LLM через Gateway</div>
              <ul className="llm-calls">
                {llmCalls.map((c, i) => (
                  <li key={i}>
                    {String(c.model ?? '')} · {String(c.purpose ?? '')} · {String(c.tokens_in ?? '')}/{String(c.tokens_out ?? '')} токенов
                    {c.cost_rub != null && ` · ${String(c.cost_rub)} ₽`}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
