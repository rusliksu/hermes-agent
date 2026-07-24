## 1. Реализация

- [x] 1.1 Создать clean worktree от exact family-access baseline
- [x] 1.2 Убрать shared memory-only toolset override
- [x] 1.3 Разрешить scoped memory binding рядом с остальными tools
- [x] 1.4 Исправить shared system prompt
- [x] 1.5 Подтвердить cache signature и owner-only approval boundary

## 2. Проверки

- [x] 2.1 Добавить behavioral test standard/shared profile parity
- [x] 2.2 Проверить unknown room deny до agent/model/tools
- [x] 2.3 Проверить private context/history/memory и cache isolation
- [x] 2.4 Проверить owner-only approvals и credential guards
- [x] 2.5 Запустить focused/full affected tests через project runner и Ruff
- [x] 2.6 Выполнить strict OpenSpec validation

## 3. Artifact и rollout

- [x] 3.1 Собрать immutable candidate и зафиксировать exact base/head/hash
- [x] 3.2 Проверить browser, safe terminal/file и negative canaries
- [x] 3.3 Зафиксировать previous symlink target и rollback command
- [x] 3.4 Атомарно переключить symlink и restart только user gateway
- [ ] 3.5 Проверить service health, masked logs и Telegram canaries
- [x] 3.6 Обновить evidence и delivery state

Service health и masked logs прошли; 3.5 остаётся открытым только до ручного
multi-participant Telegram canary, потому что credential-safe helper на HOSTKEY
недоступен.
