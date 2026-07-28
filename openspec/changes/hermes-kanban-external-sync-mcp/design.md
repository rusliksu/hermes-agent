## Контекст

Deployed checkout `/home/openclaw/.hermes/hermes-agent` сейчас указывает на
detached staging checkout с HEAD `73ba0bada88fc7e817e8a6ac73270cde201d0318` и
чистым `git status`. Локальный authoritative remote base для планирования:
`origin/HEAD -> origin/main` at `9420a10079f8ca533e6026042d5264b81d660c3e`.
Сетевой `git fetch --prune origin` не прошёл из-за DNS, поэтому свежесть
GitHub remote в этой сессии не подтверждена.

Текущий DB слой уже содержит нужную механику:

- `ExternalTaskSyncSpec` и `sync_external_task(...)`;
- транзакционный `write_txn`;
- exact `SELECT * FROM tasks WHERE external_key = ?`;
- `dry_run`;
- `expected_current_status`;
- тесты в `tests/hermes_cli/test_kanban_external_sync.py`.

Текущий MCP server `agent/transports/hermes_tools_mcp_server.py` экспонирует
curated Hermes tools для Codex runtime, включая обычные `kanban_*`, но
sync-инструмента для external-key sync там нет.

## Цели / вне целей

**Цели:**

- дать Codex runtime MCP-доступ к уже существующей exact-key Kanban sync;
- сохранить один источник sync-поведения в `hermes_cli.kanban_db`;
- требовать явный `dry_run` в MCP schema;
- требовать `expected_current_status` для `dry_run=false`;
- доказать, что title не используется для lookup;
- покрыть stale expected status rejection и отсутствие dry-run mutation;
- оставить deploy/restart/Windows MCP process вне реализации до отдельного gate.

**Вне целей:**

- не менять схему `kanban.db`;
- не добавлять batch MCP endpoint;
- не добавлять title lookup, fuzzy matching или source-path lookup;
- не добавлять новый обычный Hermes model/core tool в `tools/kanban_tools.py`;
- не добавлять зависимости;
- не читать live DB, task bodies или task results;
- не делать deploy, symlink switch, restart или запуск нового MCP процесса.

## Решения

### 1. MCP-only wrapper вместо нового Hermes model tool

Новый инструмент должен жить на краю в
`agent/transports/hermes_tools_mcp_server.py`, а не в обычном Hermes toolset.
Так Codex получает нужный MCP surface, но стандартный Hermes tool schema не
растёт для обычных agent turns.

Альтернатива: добавить `kanban_sync_external_task` в `tools/kanban_tools.py` и
включить его в `EXPOSED_TOOLS`. Это шире по surface area и противоречит цели
не создавать новый core/model tool ради Codex-only интеграции.

### 2. Все записи проходят только через `kb.sync_external_task`

Wrapper валидирует MCP-контракт, открывает `kb.connect_closing()`, вызывает
`kb.sync_external_task(...)` и возвращает `ExternalTaskSyncResult.as_dict()` как
JSON. Он не должен выполнять собственные `INSERT`, `UPDATE`, CAS или lifecycle
записи событий.

Альтернатива: переписать sync в MCP server. Это дублирует транзакционную логику
и создаёт риск divergence с CLI/tests.

### 3. Guard обязателен для мутаций

MCP schema должна требовать явный `dry_run`. Если `dry_run=false`, wrapper
должен отклонять вызов без `expected_current_status` до DB mutation. Если guard
передан и устарел, rejection должен приходить из существующего DB layer, а
после ошибки состояние задачи не меняется.

Dry-run остаётся доступным для безопасного preview и не пишет в DB.

### 4. Exact mapping без title lookup

Wrapper принимает `external_key`, `source_path`, `title`, `assignee`,
`desired_status`, опциональный `task_id` и guard-поля. `title` является только
значением для create/update, но не ключом поиска. Tests должны создать
unrelated task с тем же title и доказать, что MCP-вызов привязан только к exact
`external_key` или explicit `task_id`.

### 5. Тесты без живого MCP процесса

Focused tests должны вызывать helper/wrapper напрямую и проверять module
surface. Live stdio MCP process, Windows MCP process и deployed checkout не
нужны для baseline implementation validation и остаются отдельным delivery
gate.

## Риски / компромиссы

- [MCP tool accidentally grows normal Hermes model surface] -> держать wrapper
  MCP-only и не регистрировать его через `tools.registry`.
- [Guard обходится при apply] -> test на отсутствие `expected_current_status`
  при `dry_run=false` и stale status rejection.
- [Дублирование sync semantics] -> все writes только через `kb.sync_external_task`.
- [Title collision mutates wrong task] -> exact mapping test с одинаковым title
  у unrelated task.
- [Live process still runs old code after merge] -> отдельный post-PR gate на
  развертывание, переключение symlink, перезапуск и запуск нового Windows
  MCP-процесса.
- [Remote base freshness не доказана] -> перед implementation/PR повторить
  `git fetch --prune origin` и при успехе rebase/reset task branch на fresh
  `origin/main` до code changes.

## План миграции

1. Получить baseline approval на этот OpenSpec.
2. Перед code changes повторить fetch metadata; если GitHub доступен, обновить
   task branch относительно свежего `origin/main`.
3. Реализовать минимальный MCP-only wrapper и focused tests.
4. Запустить `scripts/run_tests.sh` для affected tests и strict OpenSpec
   validation.
5. Проверить diff и открыть task-owned PR.
6. После merge отдельно запросить live deploy/symlink/restart/new Windows MCP
   гейт запуска процесса.

## Открытые вопросы

Нет блокирующих вопросов. Единственное operational limitation: DNS до GitHub
сейчас недоступен, поэтому remote freshness нужно перепроверить перед
реализацией или PR.
