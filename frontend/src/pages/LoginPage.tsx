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
    <div className="login">
      <div className="login__brand">
        <AlfaLogo size={40} />
        <span>Альфа-Бизнес</span>
      </div>
      <div className="login__grid">
        <form className="card card--pad login__form" onSubmit={submit}>
          <h1>Вход в интернет-банк</h1>
          <p className="muted">Демонстрационный вход: в реальном банке полномочия в компании уже проверены при авторизации.</p>
          <Input label="Логин" block size={56} value={name} onChange={(_, { value }) => setName(value)} autoComplete="username" />
          <PasswordInput
            label="Пароль"
            block
            size={56}
            value={password}
            onChange={(_, { value }) => setPassword(value)}
            autoComplete="current-password"
          />
          {error && <div className="login__error">{error}</div>}
          <Button view="accent" size={56} block type="submit" loading={loading}>
            Войти
          </Button>
        </form>
        <div className="login__demo">
          <div className="login__demo-title">Демо-учётки</div>
          {DEMO_ACCOUNTS.map((a) => (
            <button
              key={a.login}
              type="button"
              className="login__account"
              disabled={loading}
              onClick={() => {
                setName(a.login);
                setPassword(a.password);
                submit(undefined, a);
              }}
            >
              <span className="login__account-title">{a.title}</span>
              <span className="login__account-text">{a.text}</span>
              <span className="login__account-creds">
                {a.login} / {a.password}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
