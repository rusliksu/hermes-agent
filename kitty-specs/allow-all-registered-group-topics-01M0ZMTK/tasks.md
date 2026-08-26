# Рабочие пакеты

## WP01 — group-wide topic inheritance

**Цель:** любой `thread_id` зарегистрированной Telegram-группы наследует её shared-room binding, сохраняя отдельный topic namespace и exact-topic overrides.
**Beads:** `tm-ai-loopx-kimi-skp`

**Одобрение implementation:** пользователь подтвердил `делай` 2026-08-26.

### Задачи

- [x] Зафиксировать full-boundary RED и точную причину расхождения deployed registry с ожидаемым parent fallback.
- [x] Реализовать минимальный config/migration или code fix без sibling-derived authority.
- [x] Добавить positive/negative regression matrix и обновить product spec.
- [x] Прогнать focused/affected suites, Ruff, `py_compile` и `git diff --check`.
- [ ] Получить независимый review; исправить замечания.
- [ ] Собрать isolated candidate, проверить rollback, перезапустить только `hermes-gateway.service` и выполнить topic canary на HOSTKEY staging. Runtime и synthetic canary пройдены; ожидается реальное сообщение в Telegram.

### Проверка завершения

Новый топик зарегистрированной группы отвечает в том же Telegram topic; неизвестные группы и неучастники остаются fail-closed; соседние topic sessions не видят историю друг друга.
