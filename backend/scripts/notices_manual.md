## Docker-образы

| Образ | Версия | Лицензия | Назначение |
|---|---|---|---|
| `python` (slim) | 3.12-slim-bookworm | PSF-2.0 | базовый образ приложения |
| `node` (Alpine) | 22-alpine | Node.js: MIT и отдельные лицензии включённых компонентов, [LICENSE](https://github.com/nodejs/node/blob/v22.x/LICENSE) | сборка фронтенда |
| `nginx` (Alpine) | 1.27-alpine | BSD-2-Clause, [LICENSE](https://nginx.org/LICENSE) | раздача фронтенда и прокси API/SSE |
| `pgvector/pgvector` | 0.8.0-pg16 | PostgreSQL License | PostgreSQL 16 + pgvector |
| `redis` | 7.4-alpine | RSALv2/SSPLv1 (Redis 7.4) | брокер очередей, pub/sub, кэш |
| `pgsty/minio` (сборка MinIO от Pigsty, включает `mc`) | RELEASE.2026-08-04T00-00-00Z | AGPL-3.0 | S3-совместимое хранилище и создание бакетов (ADR-004) |
| `tesseract-ocr`, `tesseract-ocr-rus` (Debian) | 5.x | Apache-2.0 | OCR аналитики банка (ADR-005) |

## Шрифты

| Шрифт | Лицензия | Назначение |
|---|---|---|
| Roboto Regular (`backend/data/reference/fonts/Roboto-Regular.ttf`, Google) | Apache-2.0 | генерация синтетических PDF-материалов (`make synthetic`) |
| Alfa Interface Sans, regular/medium/bold | условия правообладателя; отдельная лицензия в проекте не указана | фронтенд загружает с CDN банка, адреса в `frontend/src/styles/index.css`; файлы шрифта в репозитории отсутствуют |

## Модели

| Модель | Назначение | Лицензия / условия | Статус |
|---|---|---|---|
| `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (через fastembed) | эмбеддинги аналитики банка (`EMBEDDING_PROVIDER=local`) | Apache-2.0 | скачивается при первом старте в том `models`, в образ не входит (ADR-ML-006) |
| Gemma 4 26B A4B, GLM-5.3-Flash, Qwen3.6 35B A3B, DeepSeek-V4.1-Flash, Qwen3-VL-30B-A3B-Instruct, Kimi-K2.6 | генеративные LLM — только через LLM Gateway | условия провайдера организаторов кейса | белый список (кейс, прил. 3) |

## Датасеты

| Датасет | Источник | Условия |
|---|---|---|
| `df_Uid_data_hack.csv`, `df_Uid_payment_hack.csv`, `df_Fid_Uid_link_hack.csv` | организаторы хакатона (синтетические/обезличенные) | только для хакатона; не распространяются в репозитории |
| Аналитика Альфа-Банка (9 PDF) | организаторы хакатона | только для хакатона; не распространяются в репозитории |
| `backend/data/synthetic/*` | сгенерировано `make synthetic` | создано командой и включено в репозиторий; отдельный LICENSE проекта не задан |
| Справочник ОКВЭД2 (`data/reference/okved_classes.csv`) | ОК 029-2014 (КДЕС Ред. 2), Росстандарт — общедоступный классификатор | официальный документ, не объект авторского права (ст. 1259 ГК РФ) |

## Внешние сервисы и источники

| Сервис | Использование | Условия |
|---|---|---|
| Новостные RSS российских и зарубежных изданий | сбор новостей (перечень и статус — `docs/SOURCES.md`) | только публичные фиды; берутся заголовок, анонс и ссылка; robots.txt, User-Agent, rate limit; зарубежные ленты — условия некоммерческого использования изданий |
| Страницы новостей Минтранса, Минсельхоза, Минэкономразвития, ФНС | сбор новостей (`kind: html`, ADR-044) | открытые официальные сайты; берутся заголовок, дата и ссылка; robots.txt, User-Agent, rate limit |
| Банк России: веб-сервис DailyInfo и `XML_dynamic.asp` | ключевая ставка и официальные курсы (ADR-043) | открытые данные Банка России, ссылка на источник при показе |
| Справочник изданий (`data/reference/news_directory.json`) | поиск ленты по названию сайта в админке | составлен командой из публичных адресов RSS |
| Реестр сайтов (`data/reference/compliance_registry.json`) | фильтр законодательства РФ (ADR-037) | составлен командой по публичным реестрам Генпрокуратуры, Минюста и Роскомнадзора; сверяется вручную |
| LLM-провайдер организаторов (OpenAI-совместимый API, `https://api.alfahack-gateway26.ru/v1`) | генерация через LLM Gateway | ключ команды, лимит ~500 ₽/день |
| Telegram Bot API | алерты (`ALERTS_PROVIDER=telegram`) | условия Telegram; бот и чат — команды |

## Замоканные интеграции

| Интеграция | Как замокана | Где |
|---|---|---|
| CRM банка (заявки, обратный звонок, связь с менеджером) | запись в таблицу `crm_requests` со статусами | `backend/app/services/interactions.py`, `create_crm_request` |
| Интернет-банк «Альфа-Бизнес» (аутентификация физлица и доступ к компаниям) | демо-учётки + `user_company_access` | seed |
| Каталог продуктов банка | `bank_products`, описания из публичных страниц alfabank.ru | seed |

## Другие части решения

Зависимости ML-сервиса — [`ml/THIRD_PARTY_NOTICES.md`](ml/THIRD_PARTY_NOTICES.md); фронтенда —
[`frontend/THIRD_PARTY_NOTICES.md`](frontend/THIRD_PARTY_NOTICES.md) (перечень из `frontend/package-lock.json`).
