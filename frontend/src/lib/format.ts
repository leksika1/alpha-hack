// Подписи и форматирование для интерфейса: роли, типы событий, признаки компании, деньги и даты.
import type { ImpactLevel, Polarity, Role, StatementKind } from '../api/types';

export const ROLE_LABELS: Record<Role, string> = {
  owner: 'Собственник',
  ceo: 'Генеральный директор',
  cfo: 'Финансовый директор',
  chief_accountant: 'Главный бухгалтер',
  treasurer: 'Казначей',
  other: 'Другая роль',
};

export const ROLE_FOCUS: Record<Role, string> = {
  owner: 'стратегия, рынок, контрагенты',
  ceo: 'стратегия, рынок, регулирование',
  cfo: 'стоимость денег, ликвидность, валюта',
  chief_accountant: 'налоги, отчётность, регулирование',
  treasurer: 'ликвидность, курсы, размещение',
  other: 'сбалансированный обзор',
};

export const POLARITY: Record<Polarity, { label: string; color: 'red' | 'green' | 'orange' | 'grey' }> = {
  risk: { label: 'Риск', color: 'red' },
  opportunity: { label: 'Возможность', color: 'green' },
  mixed: { label: 'Риски и возможности', color: 'orange' },
  neutral: { label: 'Нейтрально', color: 'grey' },
};

export const IMPACT: Record<ImpactLevel, { label: string; short: string; tone: string }> = {
  low: { label: 'Низкое влияние', short: 'Низкое', tone: 'low' },
  medium: { label: 'Среднее влияние', short: 'Среднее', tone: 'medium' },
  high: { label: 'Высокое влияние', short: 'Высокое', tone: 'high' },
  critical: { label: 'Критическое влияние', short: 'Критическое', tone: 'critical' },
};

// Шкала влияния (docs/IMPACT_SCALE.md): что означает каждый уровень.
export const IMPACT_MEANING: Record<ImpactLevel, string> = {
  low: 'фоновое изменение, прямых последствий для финансов компании не ожидается',
  medium: 'заметно для отдельных статей бюджета или контрагентов, стоит учесть в планах',
  high: 'прямо затрагивает стоимость денег, выручку или ключевых контрагентов компании',
  critical: 'может существенно изменить финансовое положение компании, нужна реакция',
};

export const EVENT_TYPES: Record<string, string> = {
  monetary_policy: 'Денежно-кредитная политика',
  fx: 'Валютный рынок',
  commodities: 'Сырьевые рынки',
  sanctions: 'Санкции и ограничения',
  regulation_tax: 'Регулирование и налоги',
  trade_logistics: 'Торговля и логистика',
  industry: 'Отрасль',
  company: 'Компании',
  macro_stat: 'Макростатистика',
  labor: 'Рынок труда',
  other: 'Другое',
};

export const STATEMENT_KIND: Record<StatementKind, { label: string; hint: string }> = {
  fact: { label: 'Факт', hint: 'Подтверждено источником' },
  bank_analytics: { label: 'Аналитика Альфа-Банка', hint: 'Позиция из материалов банка' },
  model_inference: { label: 'Вывод AI', hint: 'Сформировано моделью, не позиция банка' },
  hypothesis: { label: 'Гипотеза', hint: 'Требует проверки' },
};

export const HORIZON: Record<string, string> = {
  days: 'дни',
  weeks: 'недели',
  months: 'месяцы',
  quarters: 'кварталы',
  year: 'год',
  years: 'годы',
};

export const PLAUSIBILITY: Record<string, string> = {
  low: 'низкая вероятность',
  medium: 'средняя вероятность',
  high: 'высокая вероятность',
};

export const ACTION_TYPES: Record<string, string> = {
  contact_manager: 'Связаться с менеджером',
  callback: 'Заказать звонок',
  application: 'Оставить заявку',
  analyze: 'Проанализировать',
  monitor: 'Отслеживать',
  read_source: 'Изучить источник',
};

export const REQUEST_STATUS: Record<string, { label: string; color: 'blue' | 'orange' | 'green' | 'red' | 'grey' }> = {
  new: { label: 'Принята', color: 'blue' },
  in_progress: { label: 'В работе', color: 'orange' },
  done: { label: 'Выполнена', color: 'green' },
  rejected: { label: 'Отклонена', color: 'red' },
};

export const COVERAGE: Record<string, string> = {
  direct: 'Банк напрямую оценивает это событие',
  related: 'Связанные материалы аналитики банка',
  none: 'Аналитики банка по теме нет',
};

export const SOURCE_TYPES: Record<string, string> = {
  news: 'Новость',
  bank_analytics: 'Аналитика банка',
  company_data: 'Данные о компании',
  user_data: 'Указано вами',
  market_data: 'Рыночные данные',
};

export const FEATURE_SOURCE: Record<string, string> = {
  csv: 'данные банка',
  derived: 'вычислено сервисом',
  user: 'указано вами',
  external: 'внешний источник',
};

const SEGMENTS: Record<string, string> = { large: 'Крупный бизнес', mid: 'Средний бизнес', small: 'Малый бизнес' };

const INDUSTRY_TAGS: Record<string, string> = {
  agriculture: 'сельское хозяйство',
  automotive: 'автомобили',
  automotive_parts: 'автокомпоненты',
  beverages: 'напитки',
  chemicals: 'химия',
  construction: 'строительство',
  engineering: 'инжиниринг',
  food: 'продукты питания',
  grain_processing: 'переработка зерна',
  grain_trade: 'торговля зерном',
  leasing: 'лизинг',
  logistics: 'логистика',
  machinery: 'машиностроение',
  manufacturing: 'производство',
  metals: 'металлы',
  metallurgy: 'металлургия',
  nonferrous: 'цветные металлы',
  professional_services: 'профуслуги',
  real_estate: 'недвижимость',
  rental: 'аренда',
  retail: 'розница',
  steel: 'сталь',
  trade: 'торговля',
  transport: 'транспорт',
  trucking: 'автоперевозки',
  wholesale: 'опт',
  energy: 'энергетика',
  oil_gas: 'нефть и газ',
  fuel: 'топливо',
  grain: 'зерно',
  fertilizers: 'удобрения',
  cement: 'цемент',
  development: 'девелопмент',
};

export const FEATURE_LABELS: Record<string, string> = {
  age_bucket: 'Возраст компании, лет',
  city: 'Город обслуживания',
  client_type: 'Статус клиента',
  eco_stage: 'Стадия жизненного цикла',
  foreign_ownership: 'Иностранное участие',
  industry_tags: 'Отраслевые теги',
  'okved.code': 'ОКВЭД',
  'okved.name': 'Основной вид деятельности',
  'okved.section': 'Секция ОКВЭД',
  'okved.section_name': 'Отрасль',
  owner_info_reliable: 'Сведения о собственнике',
  ownership_form: 'Форма собственности',
  region: 'Регион',
  staff_bucket: 'Численность сотрудников',
  'group.client_count': 'Клиентов банка в группе',
  'group.id': 'Группа компаний',
  'group.revenue_rub': 'Выручка группы',
  'group.size': 'Юрлиц в группе',
  revenue_rub: 'Выручка компании за 2025 г.',
  segment: 'Сегмент',
  'products.deposit': 'Депозит',
  'products.factoring': 'Факторинг',
  'products.guarantee': 'Банковская гарантия',
  'products.leasing': 'Лизинг',
  'products.loan': 'Кредит',
  'products.payroll': 'Зарплатный проект',
  'products.rko': 'Расчётный счёт',
  'sensitivities.commodity_exposure': 'Зависимость от сырья',
  'sensitivities.consumer_demand_dependent': 'Зависимость от потребспроса',
  'sensitivities.export_oriented': 'Экспортная ориентация',
  'sensitivities.fx_sensitive': 'Чувствительность к курсу',
  'sensitivities.import_dependent': 'Зависимость от импорта',
  'sensitivities.rate_sensitive': 'Чувствительность к ставке',
  'sensitivities.rate_sensitive_industry': 'Отрасль чувствительна к ставке',
  fts_countries: 'Страны ВЭД (ФТС)',
  'counterparties.amount_in_rub': 'Входящие платежи',
  'counterparties.amount_out_rub': 'Исходящие платежи',
  'counterparties.hhi_in': 'Концентрация покупателей (HHI)',
  'counterparties.hhi_out': 'Концентрация поставщиков (HHI)',
  'counterparties.n_in': 'Плательщиков',
  'counterparties.n_out': 'Получателей',
  'counterparties.self_transfer_rub': 'Переводы себе',
  'counterparties.top_in': 'Крупнейшие плательщики',
  'counterparties.top_out': 'Крупнейшие получатели',
  'counterparties.unknown_share_in': 'Доля неизвестных плательщиков',
  'counterparties.unknown_share_out': 'Доля неизвестных получателей',
  'links.shared_person_companies': 'Компании с общими физлицами',
  'links.shared_person_count': 'Связей через физлиц',
  'user.suppliers': 'Ключевые поставщики',
  'user.customers': 'Ключевые покупатели',
  'user.raw_materials': 'Сырьё и материалы',
  'user.currencies': 'Валюты расчётов',
  'user.credit_plans': 'Планы по кредитам',
  'user.notes': 'Комментарий',
};

export function featureLabel(path: string): string {
  if (FEATURE_LABELS[path]) return FEATURE_LABELS[path];
  const share = path.match(/^counterparties\.industry_share_(in|out)\.(.+)$/);
  if (share) return `${share[1] === 'in' ? 'Плательщики' : 'Получатели'}: ${INDUSTRY_TAGS[share[2]] ?? share[2]}`;
  if (path.startsWith('user_provided.') || path.startsWith('user.')) {
    const key = path.split('.').slice(1).join('.');
    return FEATURE_LABELS[`user.${key}`] ?? key;
  }
  return path;
}

export function industryTag(tag: string): string {
  return INDUSTRY_TAGS[tag] ?? tag;
}

const MONEY_PATHS = /(_rub|revenue)/;
const SHARE_PATHS = /(share|unknown_share)/;

export function featureValue(path: string, value: unknown): string {
  if (value === null || value === undefined) return 'нет данных';
  if (typeof value === 'boolean') {
    if (path.startsWith('products.')) return value ? 'подключён' : 'нет';
    if (path === 'owner_info_reliable') return value ? 'достоверные' : 'неполные';
    return value ? 'да' : 'нет';
  }
  if (typeof value === 'number') {
    if (MONEY_PATHS.test(path)) return formatMoney(value);
    if (SHARE_PATHS.test(path)) return formatPercent(value);
    return formatNumber(value);
  }
  if (path === 'segment' && typeof value === 'string') return SEGMENTS[value] ?? value;
  if (path === 'client_type' && value === 'CLIENT') return 'действующий клиент';
  if (Array.isArray(value)) {
    if (value.length === 0) return 'нет';
    if (typeof value[0] === 'string') {
      const items = value as string[];
      if (path === 'industry_tags' || path.includes('commodity')) return items.map(industryTag).join(', ');
      if (path === 'fts_countries') return items.map(capitalize).join(', ');
      return items.join(', ');
    }
    return `${value.length} записей`;
  }
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

export function segmentLabel(segment: string | null | undefined): string {
  return segment ? SEGMENTS[segment] ?? segment : '';
}

export function capitalize(s: string): string {
  const lower = s.toLocaleLowerCase('ru');
  return lower.charAt(0).toLocaleUpperCase('ru') + lower.slice(1);
}

const numberFmt = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });

export function formatNumber(n: number): string {
  return numberFmt.format(n);
}

export function formatMoney(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${numberFmt.format(n / 1e9)} млрд ₽`;
  if (abs >= 1e6) return `${numberFmt.format(n / 1e6)} млн ₽`;
  if (abs >= 1e3) return `${numberFmt.format(n / 1e3)} тыс. ₽`;
  return `${numberFmt.format(n)} ₽`;
}

export function formatPercent(share: number): string {
  return `${numberFmt.format(share * 100)}%`;
}

export function formatDate(iso: string | null | undefined, withTime = false): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'long',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  });
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso).getTime();
  const diff = Date.now() - d;
  const min = Math.round(diff / 60000);
  if (min < 1) return 'только что';
  if (min < 60) return `${min} мин назад`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} ч назад`;
  const days = Math.round(h / 24);
  if (days === 1) return 'вчера';
  if (days < 7) return `${days} дн. назад`;
  return formatDate(iso);
}

export function plural(n: number, one: string, few: string, many: string): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
}

// Имя компании без региона: «Торговля оптовая металлами · Свердловская область» → первая часть (регион показываем отдельно).
export function companyShortName(title: string): string {
  return title.split(' · ')[0];
}

export function companyInitials(title: string): string {
  const words = title.replace(/[«»"]/g, '').split(/\s+/).filter((w) => w.length > 2);
  return (words[0]?.[0] ?? 'К').toUpperCase() + (words[1]?.[0] ?? '').toUpperCase();
}

// Синтетические демо-материалы помечены в названии/издателе — показываем их как есть (docs/INTEGRATION_FRONTEND.md).
export function isSyntheticSource(src: { title?: string | null; publisher?: string | null } | undefined): boolean {
  if (!src) return false;
  return /синтетик|^ДЕМО/i.test(`${src.publisher ?? ''} ${src.title ?? ''}`.trim()) || /^ДЕМО/.test(src.title ?? '');
}

export function isDemoStatement(
  st: { kind: string; source_refs: string[] },
  sources: { id: string; type: string; title?: string | null; publisher?: string | null }[],
): boolean {
  if (st.kind !== 'bank_analytics') return false;
  const refs = st.source_refs.map((id) => sources.find((s) => s.id === id)).filter(Boolean);
  return refs.length > 0 && refs.every((s) => isSyntheticSource(s));
}

// Хвост заголовка «…: риск для компании» дублирует бадж полярности на карточке — убираем его при показе.
export function cleanTitle(title: string): string {
  return title.replace(/:\s*(риски?|возможност[ьи])(\s+и\s+(риски?|возможност[ьи]))?\s+для\s+компании\.?$/i, '');
}

// Демо-утверждения бэкенд начинает с пометки «Демо-материал (синтетика), не аналитика Альфа-Банка: …»;
// пометку показываем меткой, а из текста убираем, чтобы не повторять.
export function stripDemoPrefix(text: string): string {
  return text.replace(/^Демо-материал \(синтетика\), не аналитика Альфа-Банка(\s*\([^)]*\))?:\s*/, '');
}

export function capitalizeFirst(s: string): string {
  return s.charAt(0).toLocaleUpperCase('ru') + s.slice(1);
}

// Короткая подпись источника для сносок: аналитика банка — название документа и страница, новость — издатель.
export function sourceLabel(src: { type: string; title: string; publisher: string | null; page: number | null }): string {
  let label: string;
  if (src.type === 'bank_analytics') {
    const name = src.title.replace(/^ДЕМО \(синтетика\):\s*/, '');
    label = isSyntheticSource(src) ? `${name} (демо)` : name;
  } else if (src.type === 'news') {
    label = src.publisher || src.title;
  } else {
    label = SOURCE_TYPES[src.type] ?? src.title;
  }
  return src.page ? `${label}, с. ${src.page}` : label;
}

// Ссылки бэкенда на файлы бывают относительными (/api/v1/...): для нового окна делаем абсолютный адрес.
export function absoluteUrl(url: string): string {
  return new URL(url, window.location.origin).href;
}
