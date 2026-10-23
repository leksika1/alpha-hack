// HTTP-клиент публичного API: JWT в заголовке, обновление токена по 401, ошибки problem+json с trace_id.

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api/v1';
const STORAGE_KEY = 'alfa-future.tokens';

export interface Tokens {
  access_token: string;
  refresh_token: string;
}

export class ApiError extends Error {
  status: number;
  title: string;
  detail: string | null;
  traceId: string | null;
  type: string | null;

  constructor(status: number, body: Record<string, unknown> | null, traceHeader: string | null) {
    const title = (body?.title as string) || `Ошибка ${status}`;
    const detail = (body?.detail as string) || null;
    super(detail ? `${title}: ${detail}` : title);
    this.status = status;
    this.title = title;
    this.detail = detail;
    this.type = (body?.type as string) || null;
    this.traceId = (body?.trace_id as string) || traceHeader;
  }
}

let tokens: Tokens | null = readTokens();
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(t: Tokens | null) => void>();

function readTokens(): Tokens | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
}

export function getTokens(): Tokens | null {
  return tokens;
}

export function setTokens(next: Tokens | null): void {
  tokens = next;
  try {
    if (next) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // хранилище недоступно (приватный режим) — токены живут в памяти
  }
  listeners.forEach((fn) => fn(next));
}

export function onTokensChange(fn: (t: Tokens | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function refreshTokens(): Promise<boolean> {
  if (!tokens?.refresh_token) return false;
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/refresh`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: tokens?.refresh_token }),
        });
        if (!res.ok) return false;
        const data = (await res.json()) as Tokens;
        setTokens({ access_token: data.access_token, refresh_token: data.refresh_token ?? tokens!.refresh_token });
        return true;
      } catch {
        return false;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
  }
  return refreshing;
}

export function authHeaders(): Record<string, string> {
  return tokens ? { Authorization: `Bearer ${tokens.access_token}` } : {};
}

// Выполняет запрос с авторизацией; при 401 один раз обновляет токен и повторяет.
export async function rawFetch(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  Object.entries(authHeaders()).forEach(([k, v]) => headers.set(k, v));
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401 && retry && tokens) {
    if (await refreshTokens()) return rawFetch(path, init, false);
    setTokens(null);
  }
  return res;
}

export async function toApiError(res: Response): Promise<ApiError> {
  let body: Record<string, unknown> | null = null;
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    body = null;
  }
  return new ApiError(res.status, body, res.headers.get('X-Trace-Id'));
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await rawFetch(path, init);
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(body) });
export const patch = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });

export async function login(loginName: string, password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ login: loginName, password }),
  });
  if (!res.ok) throw await toApiError(res);
  const data = (await res.json()) as Tokens;
  setTokens({ access_token: data.access_token, refresh_token: data.refresh_token });
}

export function qs(params: Record<string, string | number | boolean | null | undefined>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  });
  const s = sp.toString();
  return s ? `?${s}` : '';
}
