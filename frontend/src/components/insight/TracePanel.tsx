// Трасса (CJM 05): какие шаги сделал агент, какие инструменты и признаки использовал, что проверил валидатор.
import { useState } from 'react';
import { ChevronDownMIcon } from '@alfalab/icons-glyph/ChevronDownMIcon';
import type { Trace } from '../../api/types';
import { featureLabel, featureValue, FEATURE_SOURCE, formatNumber } from '../../lib/format';
import { cn } from '../../lib/cn';

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

type FeatureValue = { value: unknown; source: string; note?: string | null };

export function FeatureChip({ path, f }: { path: string; f?: FeatureValue }) {
  return (
    <span className="rounded-[10px] bg-muted px-2.5 py-1 text-[13px]" title={f?.note ?? undefined}>
      {featureLabel(path)}
      {f && (
        <>
          : <b>{featureValue(path, f.value)}</b>{' '}
          <span className="ml-1 text-[11px] text-ink-3">{FEATURE_SOURCE[f.source] ?? f.source}</span>
        </>
      )}
    </span>
  );
}

export function RelevanceBreakdown({ breakdown, score }: { breakdown: Record<string, number>; score: number }) {
  const entries = Object.entries(breakdown).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...entries.map(([, v]) => v), 0.0001);
  return (
    <div className="mb-3 rounded-xl bg-muted px-4 py-3.5">
      <div className="mb-2 text-sm">
        Релевантность для компании: <b>{formatNumber(score * 100)}%</b>
      </div>
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[170px_1fr_56px] items-center gap-2.5 py-[3px] text-[13px]">
          <span>{COMPONENTS[k] ?? k}</span>
          <span className="h-2 overflow-hidden rounded bg-white">
            <span className="block h-full rounded bg-accent" style={{ width: `${(v / max) * 100}%` }} />
          </span>
          <span className="text-right text-ink-2">+{formatNumber(v * 100)}</span>
        </div>
      ))}
    </div>
  );
}

const traceLabel = 'label-caps mt-3 mb-1.5';

export function TracePanel({ trace }: { trace: Trace }) {
  const [open, setOpen] = useState(false);
  const steps = (trace.steps ?? []) as unknown as Step[];
  const run = trace.run as Record<string, unknown> | null;
  const usage = (run?.usage ?? {}) as Record<string, number>;
  const validation = trace.validation as Record<string, string>;
  const llmCalls = (trace.llm_calls ?? []) as Record<string, unknown>[];
  const features = Object.entries(trace.company_features ?? {}) as [string, FeatureValue][];

  return (
    <div className="mb-3 rounded-xl border border-line">
      <button className="flex w-full items-center justify-between px-4 py-3.5 text-left" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>
          <b>Трасса анализа</b>
          <span className="text-ink-2">
            {' '}
           : {steps.length} шагов агента, {llmCalls.length} вызовов LLM, проверка {validation?.status === 'passed' ? 'пройдена' : validation?.status ?? '—'}
          </span>
        </span>
        <ChevronDownMIcon className={cn('text-ink-2 transition-transform', open && 'rotate-180')} />
      </button>
      <div className={cn('grid transition-[grid-template-rows] duration-300 ease-alfa', open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}>
        <div className="min-h-0 overflow-hidden">
        <div className="px-4 pb-4">
          <div className="mb-3 grid gap-4 md:grid-cols-2">
            <div>
              <div className={traceLabel}>Запуск</div>
              <div className="font-mono text-[13px]">{trace.trace_id}</div>
              <div className="text-[13px] text-ink-2">
                {[
                  String(run?.generator ?? ''),
                  String(run?.outcome ?? run?.status ?? ''),
                  usage.latency_ms !== undefined ? `${usage.latency_ms} мс` : '',
                  usage.tokens_in ? `${usage.tokens_in}/${usage.tokens_out} токенов` : '',
                ]
                  .filter(Boolean)
                  .join(', ')}
              </div>
            </div>
            <div>
              <div className={traceLabel}>Проверка валидатором</div>
              <div className={validation?.status === 'passed' ? 'text-pos' : 'text-neg'}>
                {validation?.status === 'passed' ? 'Контракт и бизнес-правила соблюдены' : validation?.status}
              </div>
              <div className="text-[13px] text-ink-2">{validation?.rules}</div>
            </div>
          </div>

          {features.length > 0 && (
            <>
              <div className={traceLabel}>Признаки компании, на которые опирается вывод</div>
              <div className="my-1.5 flex flex-wrap gap-1.5">
                {features.map(([path, f]) => (
                  <FeatureChip key={path} path={path} f={f} />
                ))}
              </div>
            </>
          )}

          <div className={traceLabel}>Шаги агента</div>
          <ol>
            {steps.map((s, i) => (
              <li key={s.seq} className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 py-1.5 text-[13px]">
                <span
                  className={cn(
                    'flex size-5 shrink-0 items-center justify-center self-start rounded-full border-2 bg-white text-[10px]',
                    s.status === 'error' ? 'border-accent' : 'border-line',
                  )}
                >
                  {i + 1}
                </span>
                <span className="rounded-md bg-muted px-1.5 py-px text-[11px] font-bold">{STEP_TYPES[s.step_type] ?? s.step_type}</span>
                <span className="font-mono">
                  {s.name}
                  {s.attempt > 1 && (
                    <>
                      {' '}
                      <span className="ml-1 text-att">попытка {s.attempt}</span>
                    </>
                  )}
                </span>
                {s.latency_ms != null && <span className="ml-auto text-ink-3">{s.latency_ms} мс</span>}
                {s.output_preview && <span className="basis-full break-words text-ink-2">→ {s.output_preview}</span>}
                {s.error && <span className="basis-full text-neg">{s.error}</span>}
              </li>
            ))}
          </ol>

          {llmCalls.length > 0 && (
            <>
              <div className={traceLabel}>Вызовы LLM через Gateway</div>
              <ul className="list-disc pl-[18px] text-[13px]">
                {llmCalls.map((c, i) => (
                  <li key={i}>
                    {String(c.model ?? '')}, {String(c.purpose ?? '')}, {String(c.tokens_in ?? '')}/{String(c.tokens_out ?? '')} токенов
                    {c.cost_rub != null && `, ${String(c.cost_rub)} ₽`}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
        </div>
      </div>
    </div>
  );
}
