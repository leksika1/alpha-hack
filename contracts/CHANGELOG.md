# CHANGELOG контрактов

Формат: [semver](https://semver.org/lang/ru/). Каждое изменение ссылается на ADR.

## 1.0.0 — 2026-10-22, правка примеров (draft)

- `examples/tools/get_company_context.response.json` приведён к фактическим путям признаков:
  `counterparties.industry_share_in.<tag>` / `industry_share_out.<tag>`, `links.shared_person_companies` — список ID компаний,
  `links.shared_person_count` — число. Схемы не менялись. Полный словарь путей — `docs/DATA_MODEL.md` §company_context.

## 1.0.0 — 2026-10-22 (draft, ADR-008)

- Первая версия: `insight.schema.json`, `ml-service.openapi.yaml`, `tools-api.openapi.yaml`, `llm-gateway.openapi.yaml`, примеры.
- Статус: передаётся ML и фронту на согласование. До подтверждения допускаются правки без bump major.
