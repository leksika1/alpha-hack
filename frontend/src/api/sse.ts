// SSE поверх fetch: EventSource не умеет заголовок Authorization, поэтому поток читаем через ReadableStream.
import { rawFetch, toApiError } from './client';

export interface SseMessage {
  event: string;
  data: string;
  id: string | null;
}

export interface SseOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  onMessage: (msg: SseMessage) => void;
}

// Открывает поток и вызывает onMessage на каждое событие; завершается, когда сервер закрыл соединение.
export async function streamSse(path: string, opts: SseOptions): Promise<void> {
  const res = await rawFetch(path, {
    method: opts.method ?? 'GET',
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    headers: { Accept: 'text/event-stream', ...opts.headers },
    signal: opts.signal,
  });
  if (!res.ok) throw await toApiError(res);
  if (!res.body) return;

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let event = 'message';
  let data: string[] = [];
  let id: string | null = null;

  const dispatch = () => {
    if (data.length) opts.onMessage({ event, data: data.join('\n'), id });
    event = 'message';
    data = [];
    id = null;
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl).replace(/\r$/, '');
      buffer = buffer.slice(nl + 1);
      if (line === '') {
        dispatch();
      } else if (line.startsWith(':')) {
        // комментарий-пинг
      } else {
        const idx = line.indexOf(':');
        const field = idx >= 0 ? line.slice(0, idx) : line;
        const val = idx >= 0 ? line.slice(idx + 1).replace(/^ /, '') : '';
        if (field === 'event') event = val;
        else if (field === 'data') data.push(val);
        else if (field === 'id') id = val;
      }
    }
  }
  dispatch();
}
