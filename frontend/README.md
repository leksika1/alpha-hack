# frontend — интерфейс «Альфа-Бизнес» (зона команды фронтенда)

Здесь живёт веб-интерфейс: виджет на главном экране, лента инсайтов, карточка инсайта, профиль компании,
вопросы к аналитике. Бэкенд эту папку не меняет.

## API

- Публичное API бэкенда: `http://localhost:8000/api/v1`, Swagger — `http://localhost:8000/docs`,
  спецификация — [`../docs/openapi.json`](../docs/openapi.json).
- Гайд (экраны → эндпоинты, авторизация, SSE, ошибки problem+json) — [`../docs/INTEGRATION_FRONTEND.md`](../docs/INTEGRATION_FRONTEND.md).
- Структура инсайта — [`../contracts/insight.schema.json`](../contracts/insight.schema.json).
- Демо-учётки — в корневом `README.md`.

CORS для `http://localhost:3000` и `http://localhost:5173` включён (переменная `CORS_ORIGINS`).
