## Почему

В `hermes_cli/kanban_db.py` уже есть транзакционная синхронизация внешних задач
по точному `external_key`, включая dry-run и guard `expected_current_status`.
Codex runtime получает Hermes-возможности через
`agent/transports/hermes_tools_mcp_server.py`, но сейчас этот MCP server не
экспонирует sync-инструмент, поэтому внешняя очередь не может безопасно
проталкивать exact-key статусы через уже существующий механизм.

## Что меняется

- Добавить в Hermes tools MCP server минимальный MCP-only инструмент для
  синхронизации одной внешней Kanban-задачи через существующий
  `hermes_cli.kanban_db.sync_external_task`.
- Сохранить exact mapping: поиск и обновление только по `external_key` и
  явному `task_id`, без title lookup и без эвристик сопоставления.
- Сделать `dry_run` обязательным явным параметром MCP-вызова.
- Для mutating вызова (`dry_run=false`) требовать guard
  `expected_current_status` и передавать его в существующий DB-sync слой.
- Не добавлять новую sync-логику, новые зависимости, новую схему БД или новый
  обычный Hermes model/core tool.
- Добавить focused tests на exact mapping, dry-run без мутаций, stale expected
  status rejection и exposure MCP tool.

## Возможности

### Новые возможности

- `kanban-external-sync-mcp`: MCP-доступ к существующей exact external-key
  синхронизации Kanban-задач с обязательным dry-run флагом и status guard.

### Изменённые возможности

Нет.

## Влияние

- `agent/transports/hermes_tools_mcp_server.py`: новый MCP-only wrapper и
  регистрация в FastMCP surface.
- `tests/agent/transports/test_hermes_tools_mcp_server.py`: проверка exposure и
  поведения wrapper без живого MCP процесса.
- `tests/hermes_cli/test_kanban_external_sync.py`: при необходимости
  расширение существующего покрытия exact-key sync через MCP wrapper.
- Влияние на runtime ограничено Codex/Hermes tools MCP server. Live deploy,
  переключение symlink, restart gateway и запуск нового Windows MCP process не
  входят в этот change без отдельного gate.

## Гейты

- До реализации требуется baseline approval этого OpenSpec.
- Перед PR требуется verified focused tests и проверенный diff.
- Доставка идёт через task-owned PR.
- Live deploy/symlink/restart/new Windows MCP process требуют отдельного
  явного gate после PR.
