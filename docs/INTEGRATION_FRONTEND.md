# Гайд для фронтенда

Статус: **фаза 2 — реализовано и покрыто тестами**: auth, `/me`, смена роли, компании (профиль, анкета, граф), лента,
`/feed/summary`, инсайт, trace, state, feedback, source-click, actions (мок-CRM), продукты, новости.
Схемы — `docs/openapi.json`, Swagger — `http://localhost:8000/docs`. Ещё не реализованы (фаза 6): `GET /digest`,
`POST /ask` и `POST /insights/{id}/ask` (SSE), `GET /notifications/stream`, admin-эндпоинты.
Демо-учётки — корневой README.

## Базовые правила

- База: `http://localhost:8000/api/v1`. CORS для `localhost:3000` и `localhost:5173` (настраивается `CORS_ORIGINS`).
- Авторизация: `POST /auth/login` → `{access_token, refresh_token}`; заголовок `Authorization: Bearer <access>`; обновление — `POST /auth/refresh`.
- Ошибки: `application/problem+json` (`type`, `title`, `status`, `detail`, `trace_id`). `trace_id` показывайте в тексте ошибки — по нему мы найдём лог.
- Пагинация: `?limit=&cursor=` → `{items: [...], next_cursor: "..." | null}`.
- Время — ISO 8601 UTC. Деньги — рубли, числом.
- SSE: `GET /notifications/stream` и `POST /ask` / `POST /insights/{id}/ask` (`text/event-stream`). Для SSE с JWT используйте `fetch` + ReadableStream (EventSource не умеет заголовки).

## Экраны → эндпоинты

| Экран / элемент | Эндпоинты |
|---|---|
| Виджет на главной «Альфа-Бизнес» (постоянная точка входа) | `GET /feed/summary` (непрочитанные, срочные, топ-1 заголовок) + `GET /notifications/stream` |
| Выбор компании и роли | `GET /me`, `GET /companies`, `PUT /me/companies/{id}/role` |
| Лента | `GET /feed?company_id&role&filter=all|urgent|risks|opportunities|saved&period` |
| Карточка инсайта | `GET /insights/{id}` — блоки: «Что произошло», «Почему важно вам», «Последствия», «Что можно сделать», «Позиция банка», «На чём основано» |
| «На чём основано» / трассировка | `GET /insights/{id}/trace` |
| Открыть/скрыть/сохранить | `POST /insights/{id}/state` |
| Полезно/бесполезно | `POST /insights/{id}/feedback` |
| Перейти к источнику | `POST /insights/{id}/source-click` → URL новости или presigned URL PDF с `#page=N` (живёт 15 минут — запрашивать по клику, не кэшировать); `note` — показать подсказку |
| Материалы аналитики | `GET /analytics/documents` (название, дата, темы, страницы, метод извлечения), `GET /analytics/documents/{id}/download?page=N` → presigned URL |
| CTA (менеджер, звонок, заявка) | `POST /insights/{id}/actions` |
| Вопрос к аналитике | `POST /insights/{id}/ask`, `POST /ask` (SSE) |
| Профиль компании (что мы знаем, полнота) и анкета | `GET /companies/{id}/profile`, `PATCH /companies/{id}/profile` |
| Связи компании | `GET /companies/{id}/graph` (гипотезы помечены) |
| Дайджест | `GET /digest?period=day|week` |

## Отображение происхождения выводов (обязательно по кейсу)

`statements[].kind`: `fact` — «Факт», `bank_analytics` — «Аналитика Альфа-Банка», `model_inference` — «Вывод модели»,
`hypothesis` — «Гипотеза». Если `bank_view.model_disagrees_with_bank = true` — показать предупреждение с `disagreement_note`.
Дисклеймеры из `disclaimers[]` выводить всегда.
Синтетические демо-материалы: название начинается с «ДЕМО (синтетика)», `sources[].publisher` — «Демо-материал (синтетика),
не аналитика Альфа-Банка» — показывать как есть, не подменять на «Аналитика Альфа-Банка».
