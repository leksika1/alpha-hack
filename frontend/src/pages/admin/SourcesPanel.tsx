// Источники новостей: здоровье лент и поиск новой ленты по названию сайта или ссылке (`POST /admin/sources/discover`).
import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@alfalab/core-components/button';
import { Input } from '@alfalab/core-components/input';
import { get, post } from '../../api/client';
import type { Discover, FeedCandidate } from '../../api/types';
import { ErrorBox, H3, Note } from '../../components/ui';
import { formatDate, formatRelative } from '../../lib/format';
import { cn } from '../../lib/cn';

interface SourceHealth {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  is_fixture: boolean;
  fallback_for: string | null;
  health_status: string;
  last_success_at: string | null;
  last_error: string | null;
  items_24h: number;
}

const HEALTH_DOT: Record<string, string> = { healthy: 'bg-pos-dot', degraded: 'bg-att-dot', down: 'bg-accent' };

const OUTCOME: Record<string, string> = {
  ok: 'лента работает',
  empty: 'не лента',
  http_error: 'сайт ответил ошибкой',
  network_error: 'сайт недоступен',
  robots_disallowed: 'запрещено robots.txt',
  too_large: 'слишком большая',
};

const FOUND_BY: Record<string, string> = {
  directory: 'из справочника',
  page: 'объявлена на странице',
  link: 'сама ссылка',
  guess: 'типовой путь',
};

export function useSourcesHealth() {
  return useQuery({ queryKey: ['admin-sources'], queryFn: () => get<SourceHealth[]>('/admin/sources/health') });
}

export function SourcesHealth() {
  const sources = useSourcesHealth();
  const names = new Map((sources.data ?? []).map((s) => [s.id, s.name]));
  return (
    <>
      <H3>Источники новостей</H3>
      {sources.isError && <ErrorBox error={sources.error} onRetry={() => sources.refetch()} />}
      <div>
        {sources.data?.map((s) => (
          <div key={s.id} className="grid grid-cols-[12px_1fr_auto] items-center gap-2.5 border-b border-line py-1.5 text-sm">
            <span className={cn('size-2.5 rounded-full', HEALTH_DOT[s.health_status] ?? 'bg-ink-3')} title={s.last_error ?? undefined} />
            <span>
              {s.name}
              {s.fallback_for && <span className="text-[13px] text-ink-2"> (резерв для {names.get(s.fallback_for) ?? s.fallback_for})</span>}
            </span>
            <span className="text-[13px] text-ink-2">
              {s.items_24h} за 24 ч{s.last_success_at ? `, ${formatRelative(s.last_success_at)}` : ''}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

function Candidate({ c, onAdded }: { c: FeedCandidate; onAdded: () => void }) {
  const [added, setAdded] = useState(c.already_added);
  const add = useMutation({
    mutationFn: () => post('/admin/sources', { url: c.url, name: (c.title || c.name || c.url).slice(0, 120) }),
    onSuccess: () => {
      setAdded(true);
      onAdded();
    },
  });
  return (
    <li className="border-b border-line py-3 last:border-b-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="font-semibold">{c.title || c.name}</div>
          <a className="text-[13px] break-all" href={c.url} target="_blank" rel="noreferrer">
            {c.url}
          </a>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-ink-2">
            <span className={c.ok ? 'text-pos' : 'text-neg'}>
              {OUTCOME[c.outcome] ?? c.outcome}
              {c.status_code ? ` ${c.status_code}` : ''}
            </span>
            <span>{FOUND_BY[c.found_by] ?? c.found_by}</span>
            {c.ok && <span>новостей в ленте: {c.items}</span>}
          </div>
          {c.error && <div className="text-xs text-ink-2">{c.error}</div>}
        </div>
        {added ? (
          <span className="shrink-0 text-[13px] text-ink-2">Добавлен</span>
        ) : (
          c.ok && (
            <Button view="secondary" size={32} loading={add.isPending} onClick={() => add.mutate()}>
              Добавить
            </Button>
          )
        )}
      </div>
      {add.isError && <p className="mt-1 text-[13px] text-neg">{add.error.message}</p>}
      {c.sample.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-0.5 text-[13px]">
          {c.sample.slice(0, 3).map((s) => (
            <li key={s.url} className="truncate">
              <span className="text-ink-3">{formatDate(s.published_at)}</span> {s.title}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function DiscoverSource() {
  const qc = useQueryClient();
  const [query, setQuery] = useState('');
  const discover = useMutation({ mutationFn: (q: string) => post<Discover>('/admin/sources/discover', { query: q }) });
  const result = discover.data;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (query.trim().length >= 2) discover.mutate(query.trim());
  };

  return (
    <section className="card p-6">
      <H3>Найти источник</H3>
      <p className="mb-3 text-[13px] text-ink-2">Название сайта (например, «Ведомости») или ссылка на него: сервис найдёт ленты и проверит каждую.</p>
      <form className="flex items-end gap-2" onSubmit={submit}>
        <Input label="Сайт" block value={query} maxLength={300} onChange={(_, { value }) => setQuery(value)} />
        <Button view="accent" size={56} type="submit" loading={discover.isPending} disabled={query.trim().length < 2}>
          Найти
        </Button>
      </form>
      {discover.isError && <Note tone="warning">{discover.error.message}</Note>}
      {result?.blocked && <Note tone="warning">Сайт из реестра запрещённых ресурсов, добавить нельзя: {result.blocked}</Note>}
      {result && !result.blocked && result.candidates.length === 0 && <p className="mt-3 text-ink-2">Лент не найдено.</p>}
      {result && result.candidates.length > 0 && (
        <ul key={result.query} className="mt-2 motion-safe:animate-fade-in">
          {result.candidates.map((c) => (
            <Candidate key={c.url} c={c} onAdded={() => qc.invalidateQueries({ queryKey: ['admin-sources'] })} />
          ))}
        </ul>
      )}
    </section>
  );
}
