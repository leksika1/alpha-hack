import { useState, type FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Input } from '@alfalab/core-components/input';
import { PasswordInput } from '@alfalab/core-components/password-input';
import { Button } from '@alfalab/core-components/button';
import { useSession } from '../auth/session';
import { ApiError } from '../api/client';
import { AlfaLogo } from '../layout/AlfaLogo';

// Демо-учётки из README бэкенда — только для демонстрации прототипа.
const DEMO_ACCOUNTS = [
  { login: 'demo', password: 'demo12345', title: 'ЛПР трёх компаний', text: 'Металлоторговля (CEO), продуктовая розница (CFO), грузоперевозки (собственник)' },
  { login: 'demo2', password: 'demo12345', title: 'ЛПР двух компаний', text: 'Агрохолдинг (CEO), девелопер (казначей)' },
  { login: 'admin', password: 'admin12345', title: 'Администратор сервиса', text: 'Метрики CJM, ручная новость через конвейер' },
];

export function LoginPage() {
  const { isAuthenticated, login } = useSession();
  const navigate = useNavigate();
  const location = useLocation();
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (isAuthenticated) return <Navigate to={(location.state as { from?: string } | null)?.from ?? '/'} replace />;

  const submit = async (e?: FormEvent, creds?: { login: string; password: string }) => {
    e?.preventDefault();
    const l = creds?.login ?? name;
    const p = creds?.password ?? password;
    if (!l || !p) {
      setError('Введите логин и пароль');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await login(l, p);
      navigate(l === 'admin' ? '/admin' : '/', { replace: true });
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'Неверный логин или пароль' : err instanceof Error ? err.message : 'Ошибка входа');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[radial-gradient(1200px_600px_at_80%_-10%,rgba(239,49,36,0.12),transparent_60%)] p-8">
      <div className="mb-12 flex items-center gap-3 text-xl font-bold">
        <AlfaLogo size={40} />
        <span>Альфа-Бизнес</span>
      </div>
      <div className="grid items-start justify-center gap-8 md:grid-cols-[minmax(0,440px)_minmax(0,420px)]">
        <form className="card flex flex-col gap-4 p-6" onSubmit={submit}>
          <h1 className="text-[26px]">Вход в интернет-банк</h1>
          <p className="text-ink-2">Демонстрационный вход: в реальном банке полномочия в компании уже проверены при авторизации.</p>
          <Input label="Логин" block size={56} value={name} onChange={(_, { value }) => setName(value)} autoComplete="username" />
          <PasswordInput label="Пароль" block size={56} value={password} onChange={(_, { value }) => setPassword(value)} autoComplete="current-password" />
          {error && <div className="text-sm text-neg">{error}</div>}
          <Button view="accent" size={56} block type="submit" loading={loading}>
            Войти
          </Button>
        </form>
        <div className="flex flex-col gap-3">
          <div className="label-caps text-[13px]">Демо-учётки</div>
          {DEMO_ACCOUNTS.map((a) => (
            <button
              key={a.login}
              type="button"
              disabled={loading}
              className="flex flex-col gap-1 rounded-tile bg-white px-[18px] py-4 text-left shadow-card transition hover:-translate-y-px hover:shadow-lift"
              onClick={() => {
                setName(a.login);
                setPassword(a.password);
                submit(undefined, a);
              }}
            >
              <span className="font-bold">{a.title}</span>
              <span className="text-[13px] text-ink-2">{a.text}</span>
              <span className="font-mono text-xs text-ink-3">
                {a.login} / {a.password}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
