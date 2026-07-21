## 1. Зафиксировать область OpenSpec

- [x] 1.1 Создать repo-local change `restore-gurra-family-access` в существующем формате `openspec/changes`.
- [x] 1.2 Описать обычный семейный DM-доступ через явный `telegram_allowed_user_ids` без реальных ID или private values.
- [x] 1.3 Зафиксировать admin, pairing, elevated-действия и approval только для владельца.
- [x] 1.4 Зафиксировать неизменность двух общих групп, закрытый отказ для unknown/missing principal, диагностику со скрытием чувствительных данных, изоляцию по пользователям и отдельное live-разрешение.

## 2. Подтвердить границы реализации

- [x] 2.1 Подтвердить git evidence, что family changes не модифицируют файлы изоляции из privacy suite.
- [x] 2.2 Подтвердить, что repo-local scope не трогает active checkout, symlink, private config, systemd/service, push или deploy.

## 3. Выполнить тесты

- [x] 3.1 Через venv Python с отключёнными cache/bytecode запустить exact privacy isolation suite:
  `tests/agent/test_memory_user_id.py tests/tools/test_memory_tool.py tests/tools/test_session_search.py tests/test_hermes_state.py tests/gateway/test_session.py tests/run_agent/test_create_openai_client_kwargs_isolation.py`.
- [x] 3.2 Через venv Python с отключёнными cache/bytecode запустить group/policy affected suite:
  `tests/gateway/test_single_principal.py tests/gateway/test_telegram_group_gating.py`.
- [x] 3.3 Зафиксировать counts/results без generated `test_durations` или cache artifacts.

## 4. Подготовить additive commit

- [x] 4.1 Проверить `git status`, исключить generated cache/bytecode/test duration artifacts.
- [x] 4.2 Сделать отдельный additive commit только OpenSpec artifacts.
- [x] 4.3 Подтвердить clean worktree после commit.

## 5. Разрешение live-активации

- [ ] 5.1 Подготовить patch приватной конфигурации только после отдельного явного live-разрешения.
- [ ] 5.2 Подготовить backup/rollback readiness только после отдельного явного live-разрешения.
- [ ] 5.3 Выполнить live patch/config/restart только после отдельного явного live-разрешения.
