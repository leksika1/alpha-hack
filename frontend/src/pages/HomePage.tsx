// Главная «Альфа-Бизнеса» (имитация): остатки, дела в работе и встроенный блок аналитики.
import { Navigate } from 'react-router-dom';
import { DocumentMIcon } from '@alfalab/icons-glyph/DocumentMIcon';
import { DocumentAddMIcon } from '@alfalab/icons-glyph/DocumentAddMIcon';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { ReceiptMIcon } from '@alfalab/icons-glyph/ReceiptMIcon';
import { CardMIcon } from '@alfalab/icons-glyph/CardMIcon';
import { AnalyticsWidget } from '../components/AnalyticsWidget';
import { useSession } from '../auth/session';
import { useToast } from '../components/Toasts';
import { useRequests } from '../api/hooks';
import { REQUEST_STATUS, ACTION_TYPES, formatDate } from '../lib/format';

const TASKS: [string, number, boolean][] = [
  ['Подписать и отправить', 1, true],
  ['В работе у банка', 7, true],
  ['Отклонённые', 4, true],
  ['Черновики', 1, false],
  ['Оплатить', 1, false],
  ['Принять предложение', 3, false],
];

export function HomePage() {
  const { isAdmin, companies } = useSession();
  const toast = useToast();
  const requests = useRequests();
  if (isAdmin) return <Navigate to="/admin" replace />;

  const stub = (title: string) => toast.show({ title, body: 'Действие интернет-банка не входит в демо-прототип.', ttl: 3000 });

  return (
    <div className="home">
      <div className="quick-actions">
        <button className="quick-btn" onClick={() => stub('Скачать выписку')}>
          <DocumentMIcon /> Скачать выписку
        </button>
        <button className="quick-btn" onClick={() => stub('Импорт платежей')}>
          <DocumentAddMIcon /> Импорт платежей
        </button>
        <button className="quick-btn" onClick={() => stub('Создать платёж')}>
          <PlusMIcon /> Создать платёж
        </button>
        <button className="quick-btn quick-btn--muted" onClick={() => stub('Ещё')}>
          Ещё
        </button>
      </div>

      <section className="hero">
        <div className="hero__balance">
          <div className="hero__caption">{companies.length > 1 ? `В ${companies.length} компаниях` : 'На счетах'}</div>
          <div className="hero__amount">2 524 550 000,00 RUB</div>
          <div className="hero__sub">↳ 10 800 570 344,95 RUB с овердрафтом</div>
          <div className="hero__fx">
            <span>4 000,00 CNY</span>
            <span className="dot">·</span>
            <span>40 000,00 KZT</span>
            <span className="hero__more">+5</span>
          </div>
          <div className="hero__tiles">
            <button className="hero__tile" onClick={() => stub('Счета')}>
              <span className="hero__tile-icon">
                <ReceiptMIcon />
              </span>
              <span>
                Счета
                <small>2 в рублях, 2 в валюте</small>
              </span>
            </button>
            <button className="hero__tile" onClick={() => stub('Бизнес-карты')}>
              <span className="hero__tile-icon">
                <CardMIcon />
              </span>
              <span>
                Бизнес-карты
                <small>Реквизиты и операции</small>
              </span>
            </button>
          </div>
        </div>
        <div className="hero__tasks">
          <div className="hero__tasks-title">Дела в работе</div>
          {TASKS.map(([label, n, red]) => (
            <button key={label} className="hero__task" onClick={() => stub(label)}>
              <span>{label}</span>
              <span className={`hero__task-count ${red ? 'is-red' : ''}`}>{n}</span>
            </button>
          ))}
        </div>
      </section>

      <AnalyticsWidget />

      <div className="home__tabs">
        <span className="is-active">Сводка</span>
        <span>Остатки по компаниям</span>
        <span>Мультибанк</span>
      </div>
      <div className="home__grid">
        <section className="card card--pad">
          <div className="card__head">
            <h3>Заявки из аналитики</h3>
            <span className="muted">{requests.data?.length ?? 0}</span>
          </div>
          {!requests.data?.length && <p className="muted">Здесь появятся обращения к менеджеру, звонки и заявки, оформленные из инсайтов.</p>}
          {requests.data?.slice(0, 3).map((r) => (
            <div key={r.request_id} className="mini-row">
              <div>
                <div className="mini-row__title">{ACTION_TYPES[r.request_type] ?? r.request_type}</div>
                <div className="mini-row__sub">
                  {formatDate(r.created_at)} · {r.external_ref}
                </div>
              </div>
              <span className={`pill pill--${REQUEST_STATUS[r.status]?.color ?? 'grey'}`}>{REQUEST_STATUS[r.status]?.label ?? r.status}</span>
            </div>
          ))}
        </section>
        <section className="card card--pad">
          <div className="card__head">
            <h3>Конвертация</h3>
          </div>
          <div className="fx-table">
            <span className="muted">Валюта</span>
            <span className="muted">Продать</span>
            <span className="muted">Купить</span>
            <span>CNY/RUB</span>
            <span>9,97</span>
            <span>11,07</span>
            <span>EUR/RUB</span>
            <span>90,37</span>
            <span>91,47</span>
          </div>
        </section>
      </div>
    </div>
  );
}
