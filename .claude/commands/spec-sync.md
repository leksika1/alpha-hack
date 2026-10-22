---
description: Сверить код со SPEC.md и таблицей трассировки, устранить расхождения
---

1. Прочитай `docs/SPEC.md`, особенно §3 «Трассировка требований».
2. Для каждой строки таблицы проверь: существует ли указанный код (файл/модуль/эндпоинт) и тест; проходит ли тест.
3. Сверь `docs/openapi.json` с фактическими роутерами (`make openapi` + `git diff`), `contracts/*.json|yaml` — с Pydantic-моделями (`tests/unit/test_contracts.py`).
4. Расхождение → исправь код, либо исправь спеку **через ADR** в `docs/DECISIONS.md` (контракты — ещё и `contracts/CHANGELOG.md` + bump `schema_version`).
5. Отчёт: список расхождений и что сделано. Обнови `docs/PROGRESS.md`.
