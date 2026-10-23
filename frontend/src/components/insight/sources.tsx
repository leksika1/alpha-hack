// Переход к первоисточнику (CJM 05): новость или страница PDF аналитики по presigned-ссылке, запрашиваемой в момент клика.
import { createContext, useContext, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ModalDesktop } from '@alfalab/core-components/modal/desktop';
import { Button } from '@alfalab/core-components/button';
import { get } from '../../api/client';
import { useSourceClick } from '../../api/hooks';
import type { Source } from '../../api/types';
import { formatDate, SOURCE_TYPES } from '../../lib/format';
import { useToast } from '../Toasts';
import { Note } from '../ui';
import { cn } from '../../lib/cn';

interface NewsOut {
  news_id: string;
  title: string;
  summary: string | null;
  body: string | null;
  url: string;
  publisher: string | null;
  published_at: string;
  is_fixture: boolean;
  tags?: unknown;
}

interface SourcesCtx {
  byId: Record<string, Source>;
  open: (sourceId: string) => void;
}

const Ctx = createContext<SourcesCtx>({ byId: {}, open: () => undefined });

export function SourcesProvider({ insightId, sources, children }: { insightId: string; sources: Source[]; children: ReactNode }) {
  const click = useSourceClick(insightId);
  const toast = useToast();
  const [newsPath, setNewsPath] = useState<string | null>(null);
  const byId = Object.fromEntries(sources.map((s) => [s.id, s]));

  const open = (sourceId: string) => {
    const src = byId[sourceId];
    if (src?.type === 'company_data' || src?.type === 'user_data') {
      toast.show({ title: src.title, body: 'Признаки компании и их происхождение — во вкладке «Профиль компании» и в блоке «На чём основан вывод».', ttl: 5000 });
      return;
    }
    // Окно открываем синхронно в обработчике клика, иначе браузер заблокирует всплывающее окно.
    const win = window.open('', '_blank');
    click.mutate(sourceId, {
      onSuccess: (res) => {
        if (res.url?.startsWith('/api/v1/news/')) {
          win?.close();
          setNewsPath(res.url.replace('/api/v1', ''));
        } else if (res.url) {
          if (win) {
            win.opener = null;
            win.location.href = res.url;
          } else window.open(res.url, '_blank', 'noopener');
        } else win?.close();
        if (res.note && !res.url?.startsWith('/api/v1/news/')) toast.show({ title: res.title, body: res.note, ttl: 6000 });
      },
      onError: (e) => {
        win?.close();
        toast.show({ title: 'Источник недоступен', body: e.message, tone: 'error' });
      },
    });
  };

  return (
    <Ctx.Provider value={{ byId, open }}>
      {children}
      <NewsModal path={newsPath} onClose={() => setNewsPath(null)} />
    </Ctx.Provider>
  );
}

export function useSources() {
  return useContext(Ctx);
}

const REF_TONE: Record<string, string> = {
  bank_analytics: 'bg-neg-bg text-neg',
  company_data: 'bg-muted text-ink-2',
  user_data: 'bg-muted text-ink-2',
};

export const srcRefClass = (type: string) =>
  cn('cursor-pointer whitespace-nowrap rounded-lg px-2 py-px text-xs hover:underline', REF_TONE[type] ?? 'bg-info-bg text-info');

// Ссылка-сноска на источник: «Банк России, с. 2».
export function SourceRef({ id }: { id: string }) {
  const { byId, open } = useSources();
  const src = byId[id];
  if (!src) return null;
  return (
    <button
      type="button"
      className={srcRefClass(src.type)}
      onClick={(e) => {
        e.stopPropagation();
        open(id);
      }}
      title={`${SOURCE_TYPES[src.type] ?? src.type}: ${src.title}${src.page ? `, с. ${src.page}` : ''}`}
    >
      {src.publisher ? shortPublisher(src.publisher) : SOURCE_TYPES[src.type] ?? src.type}
      {src.page ? `, с. ${src.page}` : ''}
    </button>
  );
}

export function SourceRefs({ ids }: { ids: string[] | undefined }) {
  if (!ids?.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-[1px]">
      {ids.map((id) => (
        <SourceRef key={id} id={id} />
      ))}
    </span>
  );
}

function shortPublisher(p: string): string {
  return p.length > 34 ? `${p.slice(0, 32)}…` : p;
}

export function SourceList({ sources }: { sources: Source[] }) {
  const { open } = useSources();
  return (
    <ul className="mb-4">
      {sources.map((s) => (
        <li key={s.id}>
          <button type="button" className="grid w-full grid-cols-[140px_1fr] gap-x-3 gap-y-0.5 rounded-[10px] px-2 py-2.5 text-left hover:bg-muted" onClick={() => open(s.id)}>
            <span className={cn('row-span-2 text-xs font-bold', s.type === 'bank_analytics' ? 'text-accent' : 'text-ink-2')}>{SOURCE_TYPES[s.type] ?? s.type}</span>
            <span className="font-medium text-info">{s.title}</span>
            <span className="text-xs text-ink-2">
              {[s.publisher, s.published_at ? formatDate(s.published_at) : null, s.page ? `стр. ${s.page}` : null].filter(Boolean).join(' · ')}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function NewsModal({ path, onClose }: { path: string | null; onClose: () => void }) {
  const news = useQuery({ queryKey: ['news', path], queryFn: () => get<NewsOut>(path!), enabled: !!path });
  const n = news.data;
  const external = n?.url && !n.url.includes('fixture.local') ? n.url : null;
  return (
    <ModalDesktop open={!!path} onClose={onClose} size={600}>
      <ModalDesktop.Header hasCloser title="Первоисточник" />
      <ModalDesktop.Content>
        {news.isLoading && <p className="text-ink-2">Загружаем новость…</p>}
        {n && (
          <div>
            <div className="mb-1.5 text-[13px] text-ink-2">
              {n.publisher} · {formatDate(n.published_at, true)}
            </div>
            <h3 className="mb-2.5 text-xl">{n.title}</h3>
            {n.is_fixture && <Note>Синтетическая демо-новость — используется для офлайн-демонстрации конвейера.</Note>}
            {n.summary && <p className="mb-2 font-medium">{n.summary}</p>}
            {n.body && n.body !== n.summary && <p className="whitespace-pre-wrap">{n.body}</p>}
          </div>
        )}
      </ModalDesktop.Content>
      <ModalDesktop.Footer>
        <div className="flex gap-2">
          {external && (
            <Button view="primary" size={48} href={external} target="_blank" rel="noopener noreferrer">
              Открыть на сайте источника
            </Button>
          )}
          <Button view="secondary" size={48} onClick={onClose}>
            Закрыть
          </Button>
        </div>
      </ModalDesktop.Footer>
    </ModalDesktop>
  );
}
