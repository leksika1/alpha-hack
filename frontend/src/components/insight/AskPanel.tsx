// Вопрос к аналитике (CJM 06): ответ агента потоком (SSE) с источниками, метками происхождения и ограничениями.
import { Fragment, useEffect, useRef, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { PaperAirplaneMIcon } from '@alfalab/icons-glyph/PaperAirplaneMIcon';
import { get } from '../../api/client';
import { streamSse } from '../../api/sse';
import { keys, useAnalyticsDocuments, useAskHistory, useInsightQuestions } from '../../api/hooks';
import type { ChatFinal, QaMessage, QuestionIntent, Source, Statement } from '../../api/types';
import { absoluteUrl, formatDate, isDemoStatement, sourceLabel } from '../../lib/format';
import { Chip, KindBadge, Note } from '../ui';
import { NewsModal, srcRefClass } from './sources';
import { useToast } from '../Toasts';
import { RobotIcon } from './RobotIcon';
import { cn } from '../../lib/cn';

const INTENTS: { key: QuestionIntent; label: string }[] = [
  { key: 'clarify', label: 'Уточнить' },
  { key: 'other_slice', label: 'Другой разрез' },
  { key: 'source', label: 'Про источник' },
  { key: 'free', label: 'Свободный вопрос' },
];

const STEP_LABELS: Record<string, string> = {
  search_bank_analytics: 'Ищу в аналитике банка…',
  search_news: 'Ищу в новостях…',
  get_company_context: 'Смотрю профиль компании…',
  get_counterparties: 'Проверяю контрагентов…',
  get_group: 'Проверяю группу компаний…',
  get_event: 'Изучаю событие…',
  get_market_data: 'Смотрю рыночные данные…',
};

interface Turn {
  key: string;
  question: string;
  answer: string;
  final: ChatFinal | null;
  sources: Source[];
  statements: Statement[];
  steps: string[];
  status: 'streaming' | 'done' | 'error';
  error?: string;
  createdAt?: string;
}

function historyToTurns(messages: QaMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const m of messages) {
    if (m.role === 'user') {
      turns.push({ key: `h${m.id}`, question: m.content, answer: '', final: null, sources: [], statements: [], steps: [], status: 'done', createdAt: m.created_at });
    } else if (turns.length) {
      const t = turns[turns.length - 1];
      t.answer = m.content;
      t.sources = m.sources ?? [];
      t.statements = m.statements ?? [];
      if (m.status !== 'ok') {
        t.status = 'error';
        t.error = 'Ответ не прошёл проверку и не показан как итоговый.';
      }
    }
  }
  return turns;
}

export function AskPanel({ insightId, companyId, suggestions }: { insightId?: string; companyId: string; suggestions: string[] }) {
  const qc = useQueryClient();
  const insightHistory = useInsightQuestions(insightId);
  const companyHistory = useAskHistory(insightId ? undefined : companyId);
  const history = insightId ? insightHistory : companyHistory;
  const [live, setLive] = useState<Turn[]>([]);
  const [question, setQuestion] = useState('');
  const [intent, setIntent] = useState<QuestionIntent>('clarify');
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    setLive([]);
  }, [insightId, companyId]);

  const past = historyToTurns(history.data ?? []);
  const shownPast = live.length ? past.filter((p) => !live.some((l) => l.question === p.question)) : past;
  const turns = [...shownPast, ...live];

  const ask = async (text: string, e?: FormEvent) => {
    e?.preventDefault();
    const q = text.trim();
    if (!q || busy) return;
    setQuestion('');
    setBusy(true);
    const key = `l${Date.now()}`;
    const turn: Turn = { key, question: q, answer: '', final: null, sources: [], statements: [], steps: [], status: 'streaming' };
    setLive((xs) => [...xs, turn]);
    const update = (fn: (t: Turn) => Turn) => setLive((xs) => xs.map((t) => (t.key === key ? fn(t) : t)));
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);

    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await streamSse(insightId ? `/insights/${insightId}/ask` : '/ask', {
        method: 'POST',
        body: insightId ? { question: q, intent } : { company_id: companyId, question: q, intent },
        signal: ctrl.signal,
        onMessage: (msg) => {
          const data = msg.data ? JSON.parse(msg.data) : {};
          if (msg.event === 'step') update((t) => ({ ...t, steps: [...t.steps, data.name as string] }));
          else if (msg.event === 'token') update((t) => ({ ...t, answer: t.answer + (data.delta as string) }));
          else if (msg.event === 'source') update((t) => ({ ...t, sources: [...t.sources, data as Source] }));
          else if (msg.event === 'done') {
            const fin = data as ChatFinal;
            update((t) => ({ ...t, final: fin, answer: fin.answer, sources: fin.sources ?? t.sources, statements: fin.statements ?? [], status: 'done' }));
          } else if (msg.event === 'error') {
            update((t) => ({
              ...t,
              status: 'error',
              error: `${(data.title as string) || 'Ответ не получен'}${data.detail ? `: ${data.detail as string}` : ''}`,
            }));
          }
        },
      });
      update((t) => (t.status === 'streaming' ? { ...t, status: 'error', error: 'Соединение прервано до завершения ответа.' } : t));
    } catch (err) {
      if (!ctrl.signal.aborted) update((t) => ({ ...t, status: 'error', error: err instanceof Error ? err.message : 'Ошибка' }));
    } finally {
      setBusy(false);
      qc.invalidateQueries({ queryKey: insightId ? keys.questions(insightId) : keys.askHistory(companyId) });
      setTimeout(() => endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
    }
  };

  return (
    <div>
      {turns.length === 0 && (
        <div className="mb-3 flex items-start gap-3.5">
          <RobotIcon />
          <div>
            <b>Задайте вопрос по этому {insightId ? 'инсайту' : 'бизнесу'}</b>
            <p className="text-ink-2">
              Агент ответит с опорой на профиль компании, новости и материалы аналитики банка, покажет источники и разделит факты, позицию банка и
              выводы модели.
            </p>
          </div>
        </div>
      )}

      <div className="mb-3 flex flex-col gap-4">
        {turns.map((t) => (
          <TurnView key={t.key} turn={t} />
        ))}
        <div ref={endRef} />
      </div>

      <div className="mb-3 flex flex-wrap gap-2">
        {suggestions.map((s) => (
          <Chip key={s} disabled={busy} onClick={() => ask(s)}>
            {s}
          </Chip>
        ))}
      </div>

      <form className="rounded-tile border border-line p-2.5" onSubmit={(e) => ask(question, e)}>
        <div className="mb-2 flex flex-wrap gap-1" role="group" aria-label="Тип вопроса">
          {INTENTS.map((i) => (
            <button
              key={i.key}
              type="button"
              className={cn('rounded-[10px] px-2.5 py-1 text-[13px]', intent === i.key ? 'bg-muted-2 font-semibold text-ink' : 'text-ink-2')}
              onClick={() => setIntent(i.key)}
            >
              {i.label}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2">
          <textarea
            className="min-h-12 flex-1 resize-none px-2 py-1.5 outline-none"
            rows={2}
            maxLength={2000}
            placeholder="Например: как это повлияет на лизинговые платежи, если ставка останется до середины 2027 года?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                ask(question);
              }
            }}
          />
          <Button
            view="accent"
            size={48}
            type="submit"
            loading={busy}
            disabled={!question.trim()}
            aria-label="Отправить вопрос"
            className="!w-[48px] !min-w-[48px] shrink-0 rounded-xl !px-0 [&>span]:flex [&>span]:justify-center"
          >
            <PaperAirplaneMIcon className="block" />
          </Button>
        </div>
      </form>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const lastStep = turn.steps[turn.steps.length - 1];
  return (
    <div>
      <div className="ml-auto flex w-fit max-w-[80%] flex-col rounded-[18px_18px_4px_18px] bg-night px-3.5 py-2.5 text-white">
        <span>{turn.question}</span>
        {turn.createdAt && <span className="text-[11px] text-white/50">{formatDate(turn.createdAt, true)}</span>}
      </div>
      <div className="mt-2 max-w-[92%] rounded-[18px_18px_18px_4px] bg-muted px-4 py-3.5">
        {turn.status === 'streaming' && !turn.answer && <div className="animate-pulse text-sm text-ink-2">{STEP_LABELS[lastStep] ?? 'Анализирую…'}</div>}
        {turn.answer && <div className="whitespace-pre-wrap">{turn.answer}</div>}
        {turn.status === 'streaming' && turn.answer && <span className="inline-block h-4 w-2 animate-blink bg-accent align-[-2px]" />}
        {turn.status === 'error' && (
          <Note tone="warning">
            {turn.error} {turn.answer && 'Черновик выше не прошёл проверку бэкендом — не используйте его как итоговый ответ.'}
          </Note>
        )}
        {turn.status === 'done' && turn.statements.length > 0 && (
          <div className="mt-2.5 grid grid-cols-[max-content_1fr] items-center gap-x-3 gap-y-2.5 border-t border-black/6 pt-2.5 text-sm">
            {/* Общая сетка: колонка бейджей одной ширины, бейдж по центру своей ячейки и строки. */}
            {turn.statements.map((s) => (
              <Fragment key={s.id}>
                <KindBadge kind={s.kind} demo={isDemoStatement(s, turn.sources)} className="justify-self-center" />
                <span>{s.text}</span>
              </Fragment>
            ))}
          </div>
        )}
        {turn.status !== 'streaming' && turn.sources.length > 0 && <ChatSources sources={turn.sources} />}
        {turn.final?.disclaimers?.map((d) => (
          <p key={d.code} className="mt-2 text-xs text-ink-2">
            {d.text}
          </p>
        ))}
      </div>
    </div>
  );
}

function ChatSources({ sources }: { sources: Source[] }) {
  const docs = useAnalyticsDocuments();
  const toast = useToast();
  const [newsPath, setNewsPath] = useState<string | null>(null);

  const open = async (s: Source) => {
    if (s.type === 'news' && s.ref_id) {
      if (s.url && !s.url.includes('fixture.local')) window.open(s.url, '_blank', 'noopener');
      else setNewsPath(`/news/${s.ref_id}`);
      return;
    }
    if (s.type === 'bank_analytics') {
      const doc = docs.data?.find((d) => d.title === s.title);
      if (!doc) {
        toast.show({ title: s.title, body: 'Документ доступен во вкладке «Материалы банка».' });
        return;
      }
      const win = window.open('', '_blank');
      try {
        const res = await get<{ url: string }>(`/analytics/documents/${doc.document_id}/download${s.page ? `?page=${s.page}` : ''}`);
        if (win) {
          win.opener = null;
          win.location.href = absoluteUrl(res.url);
        }
      } catch (e) {
        win?.close();
        toast.show({ title: 'Документ недоступен', body: e instanceof Error ? e.message : '', tone: 'error' });
      }
      return;
    }
    toast.show({ title: s.title, body: 'Данные о компании — во вкладке «Профиль компании».' });
  };

  // Одинаковые подписи (та же лента, та же страница документа) показываем один раз; данные о компании — после внешних источников.
  const seen = new Set<string>();
  const unique = sources.filter((s) => {
    const label = sourceLabel(s);
    if (seen.has(label)) return false;
    seen.add(label);
    return true;
  });
  const isOwnData = (s: Source) => s.type === 'company_data' || s.type === 'user_data';
  const ordered = [...unique.filter((s) => !isOwnData(s)), ...unique.filter(isOwnData)];

  return (
    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
      <span className="text-[13px] text-ink-2">Источники:</span>
      {ordered.map((s) => (
        <button key={s.id} type="button" className={srcRefClass(s.type)} onClick={() => open(s)} title={s.title}>
          {sourceLabel(s)}
        </button>
      ))}
      <NewsModal path={newsPath} onClose={() => setNewsPath(null)} />
    </div>
  );
}
