## 1. Базовый гейт

- [x] 1.1 Получить explicit baseline approval на proposal/design/specs/tasks до implementation.
- [x] 1.2 Перед code changes повторить `git fetch --prune origin` и подтвердить fresh `origin/main`; если сеть доступна, обновить task branch от fresh base.

## 2. Контракт MCP

- [x] 2.1 Добавить MCP-only wrapper в `agent/transports/hermes_tools_mcp_server.py` без регистрации в `tools.registry`.
- [x] 2.2 Описать MCP schema с обязательными `external_key`, `source_path`, `title`, `assignee`, `desired_status`, `dry_run` и guard-полями.
- [x] 2.3 Отклонять `dry_run=false` без `expected_current_status` до DB mutation.
- [x] 2.4 В wrapper вызывать только `hermes_cli.kanban_db.sync_external_task` для записей и возвращать JSON из `ExternalTaskSyncResult.as_dict()`.

## 3. Тесты

- [x] 3.1 Добавить exact mapping test: task с тем же title не выбирается без exact `external_key` или explicit `task_id`.
- [x] 3.2 Добавить dry-run test: MCP wrapper возвращает planned result и не пишет task/run/event rows.
- [x] 3.3 Добавить stale expected status test: mismatch отклоняется и состояние задачи не меняется.
- [x] 3.4 Добавить exposure test в `tests/agent/transports/test_hermes_tools_mcp_server.py`.

## 4. Проверки и PR

- [x] 4.1 Запустить affected tests через `scripts/run_tests.sh`.
- [x] 4.2 Проверить `git diff` на отсутствие новой sync-логики, dependencies, DB schema changes, deploy/restart scripts и credential/task-body output.
- [x] 4.3 Запустить strict OpenSpec validation.
- [x] 4.4 Открыть task-owned PR после зелёных проверок и verified diff.

## 5. Отдельный live gate

- [ ] 5.1 После PR отдельно запросить гейт на развертывание, переключение
  symlink, перезапуск и запуск нового Windows MCP-процесса.
- [ ] 5.2 Выполнять live rollout только после отдельного approval и с фиксированным rollback target.

## Результаты implementation packet

- Baseline approval: пользователь явно запросил implementation уже одобренного
  OpenSpec change 2026-07-27.
- Fresh-base evidence (2026-07-28, около 06:11 UTC): независимый host-level
  `git fetch --prune origin` успешно завершён; удалена только устаревшая ветка
  `origin/codex/gurra-topic-scoped-memory`. После fetch локальные `HEAD` и
  `origin/main` равны `9420a10079f8ca533e6026042d5264b81d660c3e`, а
  `git rev-list --left-right --count HEAD...origin/main` вернул `0 0`;
  пересечений нет.
- Тесты: `scripts/run_tests.sh tests/agent/transports/test_hermes_tools_mcp_server.py tests/hermes_cli/test_kanban_external_sync.py`
  — 41 успешно, 0 сбоев.
- Compile: shared project venv `python -m py_compile` для изменённых Python
  файлов — успешно.
- Diff: `git diff --check` — успешно; dependencies, DB schema,
  deploy/restart scripts и обычная Hermes tool registration не менялись.
- Проверка: `openspec validate hermes-kanban-external-sync-mcp --type change --strict --no-interactive`
  — успешно.
- PR: task-owned draft PR https://github.com/rusliksu/hermes-agent/pull/12
  создан из head `9264b53d65990f0fe34d6278626657e911000a66` в `main` после
  зелёных проверок; live rollout не выполнялся.
