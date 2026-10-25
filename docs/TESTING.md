# Тестирование

Последний проверенный результат и границы прогона: [VERIFICATION.md](VERIFICATION.md).

| Уровень | Команда | Что проверяет | Окружение |
|---|---|---|---|
| Lint и типы | `make lint`, `make typecheck` | ruff (правила и формат), mypy strict для `app/domain` и `app/services` | локально |
| Unit | `make test` | доменная логика без внешних сервисов: очистка данных, контекст компании, тегер, кластеризация, скорер, валидатор, нарезка PDF, Gateway (маскирование, кэш, оценки), проверка ответа агента, коннекторы новостей (respx) | локально, ~10 с |
| Integration | `make integration` | сервисы на настоящих Postgres + pgvector, Redis, MinIO (testcontainers): загрузка данных, конвейер новостей, аналитика (OCR-путь, поиск), Gateway (бюджет под конкуренцией, rate limit), полный API-поток с ml-stub в процессе | Docker, ~1 мин |
| E2E | `make e2e` | весь compose-стек в профиле `test`: seed → очередь → воркер → ml-stub → Tools API → лента; сценарии SPEC §11.2 | Docker, ~5 мин со сборкой |
| Backend + ML | `make e2e-ml` | настоящий ML-сервис: генерация через очередь и Tools API, валидированный инсайт и трасса, SSE-вопрос, авторизация и `/v1/score` | Docker; правила ML без платного LLM и скачивания моделей |

`make check` = lint + типы + unit + integration. CI (`.github/workflows/ci.yml`) на каждый push в `main`/`dev`
и на PR запускает задания `lint-type-unit`, `integration`, `ml`, `frontend`, `e2e`. Задание `ml` проверяет
собственный код и тесты ML; `frontend` собирает образ фронтенда; `e2e` проверяет compose-файлы частей в режиме
Dokploy, сценарии с заглушкой и `make e2e-ml` с настоящим ML-сервисом.
После зелёных заданий на push в `main`/`dev` задание `deploy`
вызывает вебхуки Dokploy (`docs/RUNBOOK.md`, «Деплой»).

## Окружение e2e (`backend/docker-compose.test.yml`)

- **Данные:** синтетика (`data/synthetic`), демо-лента новостей, `EMBEDDING_PROVIDER=fake`, `OCR_PROVIDER=fake`,
  `LLM_PROVIDER=fake` (детерминированные ответы через настоящий Gateway).
- **Источники новостей:** каталог `data/synthetic/news/e2e_sources.json`. Основной источник на домене `.invalid`
  недоступен, резерв локальный; из стека ничего не уходит в сеть (ADR-031).
- **Бюджет LLM** — 0,3 ₽: последний тест его исчерпывает и проверяет деградацию.
- **Порты наружу** не публикуются, проект `alfa-e2e` изолирован от демо-стенда.

`make e2e-ml` использует дополнительный `backend/docker-compose.ml-test.yml` и отдельный проект `alfa-ml-e2e`.
В нём `INSIGHT_GENERATOR=remote`, ML работает с `ML_LLM_ENABLED=false`, образ собирается с `SKIP_MODELS=1`.
Это проверка реального HTTP-протокола между частями и правил агента. Качество ответов платного провайдера
и семантической модели этим прогоном не измеряется. Оба e2e-таргета удаляют только свои временные тома.

## Сценарии SPEC §11.2 → тесты

| № | Сценарий | Тест (`backend/tests/e2e/`) |
|---|---|---|
| 1 | Логин → компании → профиль с признаками и полнотой | `test_flow.py::test_scenario_1_login_companies_profile` |
| 2 | Лента не пуста, отсортирована; дисклеймер, источники, `kind`; ленты разных отраслей различаются | `test_flow.py::test_scenario_2_feed_is_personalized_and_valid` |
| 3 | Смена роли меняет вариант инсайта | `test_scenarios.py::test_scenario_3_role_switch_changes_variant` |
| 4 | Трассировка: признаки, источники, шаги агента | `test_flow.py::test_scenario_4_trace`, `test_agent_llm_calls_are_traced` |
| 5 | Скрыть → пропадает; «нерелевантно» видно в метриках | `test_scenarios.py::test_scenario_5_hidden_insight_disappears`, `test_flow.py::test_scenario_5_feedback_is_visible_in_admin_metrics` |
| 6 | Переход к источнику: новость и страница PDF | `test_scenarios.py::test_scenario_6_sources_news_and_pdf_page`, `test_flow.py::test_bank_analytics_indexed_and_cited` |
| 7 | CTA «обратный звонок» → мок-CRM | `test_flow.py::test_scenario_7_callback_goes_to_mock_crm` |
| 8 | Вопрос → SSE-ответ с источниками | `test_flow.py::test_scenario_8_question_gets_sse_answer_with_sources` |
| 9 | Срочная новость → конвейер → SSE-уведомление → `/feed/summary` вырос | `test_scenarios.py::test_scenario_9_urgent_news_reaches_the_user_over_sse`, `test_flow.py::test_notifications_stream_and_digest` |
| 10 | Дополнение профиля → перегенерация меняет релевантность | `test_scenarios.py::test_scenario_10_profile_update_changes_relevance` |
| 11 | Чужая компания → 403 | `test_flow.py::test_scenario_11_foreign_company_is_forbidden` |
| 11 | Запрещённая модель → 403 | `test_flow.py::test_scenario_11_llm_gateway_allowlist_and_budget` |
| 11 | Бюджет исчерпан → деградация | `test_scenarios.py::test_scenario_11_exhausted_llm_budget_degrades_gracefully` |
| 11 | Источник down → резерв | `test_scenarios.py::test_scenario_11_source_down_switches_to_reserve` |
| 11 | Prompt injection не ломает контракт | `test_scenarios.py::test_scenario_11_prompt_injection_does_not_break_the_contract` |
| 12 | Неизвестная новость проходит конвейер | `test_scenarios.py::test_scenario_12_unknown_news_passes_the_pipeline` |

Тесты выполняются по порядку на одном стеке и меняют его состояние: скрытия, роль, анкета, бюджет.
Поэтому тест бюджета стоит последним, а роль после сценария 3 возвращается обратно.

## Правила

- Тесты не ослабляются, чтобы пройти; моки — только в тестах или за флагами `*_PROVIDER=fake`.
- Integration и e2e не ходят в интернет: внешние HTTP — через respx или домены `.invalid`.
- Новая функция — unit на правила, integration на работу с БД/S3/очередями, e2e — если меняет пользовательский
  сценарий.
