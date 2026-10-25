# Интеграция фронтенда

Все эндпоинты ниже реализованы и покрыты тестами. Схемы — `docs/openapi.json`, Swagger —
`http://localhost:8000/docs`. Демо-учётки — корневой README.

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
| Виджет на главной «Альфа-Бизнес» (постоянная точка входа, CJM 01) | `GET /feed/summary` (непрочитанные, срочные, топ-1 с `why_for_you`) + `GET /notifications/stream`; показ и клик — `POST /events` (`widget_shown`, `widget_click`) |
| Выбор компании и роли | `GET /me`, `GET /companies`, `PUT /me/companies/{id}/role` |
| Лента (CJM 03) | `GET /feed?company_id&role&filter=all|urgent|risks|opportunities|saved|hidden&period`; в карточке `why_for_you` («почему это для вас»), `priority_reasons` («почему выше»), `state.updated` («обновлено»); в фильтре `all` одной макротемы (ставка ЦБ, курс) — не больше `FEED_TOPIC_CAP` карточек, `more_in_topic` > 0 — «ещё N по теме» (полный список — в фильтрах) |
| Карточка инсайта | `GET /insights/{id}?via=feed&position=N` (via/position — для метрик воронки) — блоки: «Что произошло», «Почему важно вам», «Последствия», «Что можно сделать», «Позиция банка», «На чём основано» |
| «На чём основано» / трассировка | `GET /insights/{id}/trace` |
| Открыть/скрыть/сохранить/наблюдать | `POST /insights/{id}/state` (`watch` — «наблюдать», CJM 07) |
| Полезно/бесполезно (CJM 08) | `POST /insights/{id}/feedback` → `applied[]` — показать, что сделано с отзывом; `hidden=true` при «нерелевантно» (вернуть — `unhide`) |
| Перейти к источнику | `POST /insights/{id}/source-click` → URL новости или относительная ссылка на PDF с `#page=N` (отдаёт API, живёт 15 минут — запрашивать по клику, не кэшировать); `note` — показать подсказку |
| Материалы аналитики | `GET /analytics/documents` (название, дата, темы, страницы, метод извлечения), `GET /analytics/documents/{id}/download?page=N` → ссылка на PDF (открывать как есть, без заголовка Authorization) |
| CTA (менеджер, звонок, заявка) | `POST /insights/{id}/actions`; статус — `GET /me/requests` и уведомление `request_update` |
| Вопрос к аналитике (CJM 06) | `POST /insights/{id}/ask`, `POST /ask` — SSE (см. ниже); история — `GET /insights/{id}/questions`, `GET /ask/history?company_id` |
| Уведомления (CJM 08) | `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all`, `GET /notifications/stream` (SSE) |
| Профиль компании (что мы знаем, полнота) и анкета | `GET /companies/{id}/profile`, `PATCH /companies/{id}/profile` |
| Связи компании | `GET /companies/{id}/graph` (гипотезы помечены) |
| Дайджест | `GET /digest?company_id&period=day|week` — счётчики, топ-5, риски, возможности, следующие шаги |
| «Прислать новость» | `POST /news/submit {company_id, url}` или `{company_id, title, text}` → показать `message`; `unavailable` — «Ресурс сейчас недоступен для просмотра» (+ `reason`); `blocked` — нарушает законодательство (+ `reason` со статьёй); `generation_queued` — инсайт придёт в ленту и уведомлением |
| Админ: материалы аналитики | `POST /admin/analytics?file_name=x.pdf` — тело запроса сам файл (`Content-Type: application/pdf`, без multipart) → 202 со статусом `processing`; список и статусы обработки — `GET /admin/analytics` (опрашивать, пока `processing`) |
| Админ: генерация и проблемы | `GET /admin/generation` (очередь, запуски, ошибки, доступность ML), `POST /admin/generation/retry`; `GET /admin/alerts`; `GET /admin/llm-usage`; `POST /admin/news/cleanup?days=N` |
| Админ: источники новостей | `GET /admin/sources`; поиск по названию или сайту — `POST /admin/sources/discover {query}` (кандидаты с `ok`, `items`, `sample`, `already_added`); перед добавлением — `POST /admin/sources/check {url}` (показать `sample` — последние новости ленты, `outcome`/`error`); `POST /admin/sources {url,name,topics}`; `PATCH /admin/sources/{id} {enabled}`; `DELETE` — только `origin=admin` |

## SSE: вопросы и уведомления

- Авторизация — заголовок `Authorization: Bearer`, поэтому вместо `EventSource` используйте `fetch` со стримингом
  (например, `@microsoft/fetch-event-source`). Токен в URL не принимаем.
- **Вопрос** (`POST /insights/{id}/ask`, `POST /ask`): события `run` (`run_id`, `question_id`) → `step`/`token`/`source`
  (от агента, показывать по мере прихода) → `done` (ответ, `sources`, `statements` с `kind`, дисклеймеры — после
  проверки бэкендом) **или** `error` (problem+json: `ml-unavailable`, `agent-timeout`, `invalid-answer`). Если пришёл
  `error` после токенов — показать, что ответ не прошёл проверку, и не выдавать его за итоговый.
- **Уведомления** (`GET /notifications/stream`): `ready` (`unread`, `last_id`) → `notification` (с `id:`) и пинги-комментарии.
  Соединение живёт до `max_s` (300 с по умолчанию); при обрыве переподключаться с заголовком `Last-Event-ID` —
  придут пропущенные. Виды: `urgent_insight` (срочный или с высоким влиянием), `digest`, `request_update`.

## Отображение происхождения выводов (обязательно по кейсу)

`statements[].kind`: `fact` — «Факт», `bank_analytics` — «Аналитика Альфа-Банка», `model_inference` — «Вывод модели»,
`hypothesis` — «Гипотеза». Если `bank_view.model_disagrees_with_bank = true` — показать предупреждение с `disagreement_note`.
Дисклеймеры из `disclaimers[]` выводить всегда.
Синтетические демо-материалы: название начинается с «ДЕМО (синтетика)», `sources[].publisher` — «Демо-материал (синтетика),
не аналитика Альфа-Банка» — показывать как есть, не подменять на «Аналитика Альфа-Банка».
