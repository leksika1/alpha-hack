// Вопрос к аналитике (CJM 06): ответ агента потоком (SSE) с источниками, метками происхождения и ограничениями.
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { PaperAirplaneMIcon } from '@alfalab/icons-glyph/PaperAirplaneMIcon';
import { get } from '../../api/client';
import { streamSse } from '../../api/sse';
import { keys, useAnalyticsDocuments, useAskHistory, useInsightQuestions } from '../../api/hooks';
import type { ChatFinal, QaMessage, QuestionIntent, Source, Statement } from '../../api/types';
import { formatDate, isDemoStatement, SOURCE_TYPES } from '../../lib/format';
import { KindBadge } from '../ui';
import { NewsModal } from './sources';
import { useToast } from '../Toasts';
import { RobotIcon } from './RobotIcon';

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

export function AskPanel({
  insightId,
  companyId,
  suggestions,
  compact = false,
}: {
  insightId?: string;
  companyId: string;
  suggestions: string[];
  compact?: boolean;
}) {
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
    <div className={`ask ${compact ? 'ask--compact' : ''}`}>
      {turns.length === 0 && (
        <div className="ask__intro">
          <RobotIcon />
          <div>
            <b>Задайте вопрос по этому {insightId ? 'инсайту' : 'бизнесу'}</b>
            <p className="muted">
              Агент ответит с опорой на профиль компании, новости и материалы аналитики банка, покажет источники и разделит факты,
              позицию банка и выводы модели.
            </p>
          </div>
        </div>
      )}

      <div className="ask__turns">
        {turns.map((t) => (
          <TurnView key={t.key} turn={t} />
        ))}
        <div ref={endRef} />
      </div>

      <div className="ask__suggest">
        {suggestions.map((s) => (
          <button key={s} type="button" className="chip" disabled={busy} onClick={() => ask(s)}>
            {s}
          </button>
        ))}
      </div>

      <form className="ask__form" onSubmit={(e) => ask(question, e)}>
        <div className="ask__intents" role="group" aria-label="Тип вопроса">
          {INTENTS.map((i) => (
            <button key={i.key} type="button" className={`ask__intent ${intent === i.key ? 'is-active' : ''}`} onClick={() => setIntent(i.key)}>
              {i.label}
            </button>
          ))}
        </div>
        <div className="ask__row">
          <textarea
            className="ask__input"
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
          <Button view="accent" size={48} type="submit" loading={busy} disabled={!question.trim()} aria-label="Отправить вопрос">
            <PaperAirplaneMIcon />
          </Button>
        </div>
      </form>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  const lastStep = turn.steps[turn.steps.length - 1];
  return (
    <div className="turn">
      <div className="turn__q">
        <span>{turn.question}</span>
        {turn.createdAt && <span className="turn__time">{formatDate(turn.createdAt, true)}</span>}
      </div>
      <div className={`turn__a turn__a--${turn.status}`}>
        {turn.status === 'streaming' && !turn.answer && <div className="turn__step">{STEP_LABELS[lastStep] ?? 'Анализирую…'}</div>}
        {turn.answer && <div className="turn__text">{turn.answer}</div>}
        {turn.status === 'streaming' && turn.answer && <span className="turn__cursor" />}
        {turn.status === 'error' && (
          <div className="note note--warning">
            {turn.error} {turn.answer && 'Черновик выше не прошёл проверку бэкендом — не используйте его как итоговый ответ.'}
          </div>
        )}
        {turn.status === 'done' && turn.statements.length > 0 && (
          <div className="turn__statements">
            {turn.statements.map((s) => (
              <div key={s.id} className="statement statement--small">
                <KindBadge kind={s.kind} demo={isDemoStatement(s, turn.sources)} />
                <span>{s.text}</span>
              </div>
            ))}
          </div>
        )}
        {turn.status !== 'streaming' && turn.sources.length > 0 && <ChatSources sources={turn.sources} />}
        {turn.final?.disclaimers?.map((d) => (
          <p key={d.code} className="turn__disclaimer">
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
          win.location.href = res.url;
        }
      } catch (e) {
        win?.close();
        toast.show({ title: 'Документ недоступен', body: e instanceof Error ? e.message : '', tone: 'error' });
      }
      return;
    }
    toast.show({ title: s.title, body: 'Данные о компании — во вкладке «Профиль компании».' });
  };

  return (
    <div className="turn__sources">
      <span className="muted small">Источники:</span>
      {sources.map((s) => (
        <button key={s.id} type="button" className={`src-ref src-ref--${s.type}`} onClick={() => open(s)} title={s.title}>
          {SOURCE_TYPES[s.type] ?? s.type}: {s.title.length > 48 ? `${s.title.slice(0, 46)}…` : s.title}
          {s.page ? `, с. ${s.page}` : ''}
        </button>
      ))}
      <NewsModal path={newsPath} onClose={() => setNewsPath(null)} />
    </div>
  );
}
