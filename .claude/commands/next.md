---
description: Взять следующую невыполненную задачу из PROGRESS.md и выполнить её по DoD
---

1. Прочитай `CLAUDE.md` и `docs/PROGRESS.md`.
2. Возьми первую задачу со статусом `[ ]` в текущей фазе (или указанную в «Следующий шаг»). Отметь её `[~]` (в работе).
3. Сверься с `docs/SPEC.md` (раздел, на который ссылается задача) и `contracts/`, если задача их касается.
4. Выполни задачу по Definition of Done (`docs/agent/RULES.md` §DoD): код + тесты + `make check` зелёный + docs/OpenAPI + PROGRESS.
5. Пробелы в спеке закрой ADR в `docs/DECISIONS.md`. Всё, что вне задачи, — в `docs/BACKLOG.md`.
6. Обнови `docs/PROGRESS.md` (статус `[x]`, «Текущая задача», «Следующий шаг», блокеры).
7. Закоммить (Conventional Commits). Не пушь с `--force`.
