## ADDED Requirements

### Requirement: MCP server экспонирует guarded external Kanban sync

Hermes tools MCP server MUST экспонировать MCP-only инструмент для
синхронизации одной внешней Kanban-задачи через уже существующий
транзакционный путь `hermes_cli.kanban_db.sync_external_task`.

#### Scenario: Инструмент виден в Hermes tools MCP surface

- **WHEN** `agent/transports/hermes_tools_mcp_server.py` строит tool surface
- **THEN** external sync MCP tool зарегистрирован для MCP clients
- **AND** он не зарегистрирован как обычный Hermes model/core tool

#### Scenario: Инструмент делегирует существующему DB sync

- **WHEN** MCP client вызывает инструмент с валидными sync parameters
- **THEN** реализация открывает Kanban DB через существующие connection helpers
  из `hermes_cli.kanban_db`
- **AND** все записи выполняются через `sync_external_task`
- **AND** MCP server не реализует дублирующую insert/update sync-логику

### Requirement: MCP sync требует explicit dry-run intent и status guard

MCP tool MUST требовать переданный caller-ом boolean `dry_run`. Для mutating
вызовов, где `dry_run=false`, инструмент обязан требовать
`expected_current_status` до DB mutation.

#### Scenario: Dry-run показывает план без мутации

- **WHEN** MCP tool вызван с `dry_run=true`
- **THEN** результат возвращает planned `ExternalTaskSyncResult`
- **AND** Kanban task row, run row и event row не записываются

#### Scenario: Mutating вызов без expected status отклоняется

- **WHEN** MCP tool вызван с `dry_run=false` и без
  `expected_current_status`
- **THEN** вызов отклоняется до любой Kanban DB mutation

#### Scenario: Устаревший expected status отклоняется без мутации

- **WHEN** MCP tool вызван с `dry_run=false`, и `expected_current_status` не
  совпадает с текущим статусом exact-key task
- **THEN** вызов возвращает ошибку из существующего sync guard
- **AND** status, title, assignee, external key и source path задачи остаются
  неизменными

### Requirement: MCP sync использует только exact external identity

MCP-инструмент MUST сопоставлять внешние задачи по точному `external_key` или
по правилам привязки явно указанного `task_id`, уже реализованным в
`sync_external_task`. Инструмент не должен находить, выбирать или обновлять
задачи по заголовку.

#### Scenario: Одинаковый title не выбирает unrelated task

- **WHEN** несвязанная Kanban-задача имеет тот же заголовок, что и данные
  входящей внешней задачи
- **AND** MCP-инструмент вызван с новым точным `external_key`
- **THEN** несвязанная задача не обновляется
- **AND** результат синхронизации относится только к задаче, выбранной или
  созданной по точному `external_key` или явно указанному `task_id`

#### Scenario: Existing exact key обновляет ту же задачу

- **WHEN** MCP tool вызван для `external_key`, уже attached к задаче
- **THEN** existing task с этим exact key обновляется через
  `sync_external_task`
- **AND** title lookup не выполняется

### Requirement: Delivery gates отделены от implementation

Реализация MCP tool MUST NOT выполнять deploy, переключать symlink,
перезапускать Hermes services или стартовать новый Windows MCP process без
отдельного explicit live gate.

#### Scenario: Baseline approval предшествует implementation

- **WHEN** этот OpenSpec complete
- **THEN** implementation не начинается, пока не записан baseline approval

#### Scenario: Live rollout требует отдельный gate

- **WHEN** tests pass, diff verified, и task-owned PR ready или merged
- **THEN** live deploy, symlink switch, restart и new Windows MCP process
  остаются blocked до отдельного approval
