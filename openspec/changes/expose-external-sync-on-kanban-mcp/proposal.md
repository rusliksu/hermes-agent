## Почему

Активный Windows connector запускает отдельный `hermes mcp serve-kanban
--allow-write`, однако выделенный Kanban MCP adapter с фактической поверхностью
из 10 инструментов отсутствует в актуальном `main`. После PR #12 guarded
`kanban_sync_external_task` существует только в другом MCP server, поэтому
активная Kanban MCP поверхность не получает уже принятую exact-key
синхронизацию.

Независимое финальное ревью текущей незакоммиченной реализации вынесло
вердикт `BLOCK`. После предыдущего architecture delta остались три
load-bearing проблемы: read-only соединение с `immutable=1` может не видеть
зафиксированные, но ещё не checkpointed изменения активной WAL; dependency
ветки `block_task` вызывают lifecycle hook внутри write-транзакции до
`COMMIT`; политика stdio framer недостаточно определяет остаточный frame на
EOF, несколько frames, пустые строки и продолжение после malformed frame.
Поэтому продолжение реализации потребовало новый material delta и повторное
явное одобрение; точный WAL-контракт этого delta одобрен.

Эта реализация уже доставлена отдельным PR #15 на exact commit
`062f2f0f1f6947830d1b222a3ef470e145a7c34d`, но live rollout не выполнялся.
Оставшийся ручной этап переключения standalone Kanban MCP wrapper не имеет
репозиторного dry-run helper с проверкой текущего wrapper hash и
воспроизводимым rollback. Одобренный material delta добавляет сначала
отдельный helper PR; текущий запуск ограничен обновлением OpenSpec и не
реализует helper и не касается live среды.

## Что меняется

- Минимально перенести на актуальный `main` выделенный
  `agent.transports.hermes_kanban_mcp_server` и CLI wiring
  `hermes mcp serve-kanban` из focused commits `4b4a07d25`, `82c3597f8`,
  `4cd6b4318`, `db1af8ebd`, `db0c7d7e4`.
- Переносить только относящиеся к Kanban MCP adapter изменения вручную или
  узким патчем; не merge и не cherry-pick старую ветку целиком.
- Сохранить фактические 10 инструментов, read-only/write gating,
  русскоязычную policy и обработку нескольких stdio-сообщений, пришедших одним
  блоком.
- Добавить одиннадцатый write-only инструмент
  `kanban_sync_external_task`, переиспользующий существующий
  `sync_external_task` и guards из PR #12: точный `external_key`, обязательный
  явный `dry_run`, обязательный `expected_current_status` при записи и
  отсутствие title lookup.
- Вынести custom stdio byte-buffer, framing и lifecycle в нейтральный
  `agent/transports/hermes_kanban_mcp_stdio.py`; adapter сохраняет только
  allow-listed handlers/registration, становится меньше 1000 строк и не может
  снова превысить этот лимит без новой extraction.
- Вынести `KANBAN_EXTERNAL_SYNC_TOOL` и `kanban_sync_external_task` в
  нейтральный shared MCP boundary
  `agent/transports/kanban_external_sync_mcp.py`. Оба MCP server используют
  одну реализацию; прежний server может re-export symbols для обратной
  совместимости, но guards и SQL не дублируются.
- Перенести атомарный exact-key upsert OpenSpec source definitions из importer
  в узкий публичный batch API
  `hermes_cli.kanban_db.upsert_openspec_task_definitions`. Importer только
  разбирает OpenSpec, формирует specs и вызывает API; API сохраняет runtime
  ownership fields и пишет canonical audit events только при создании или
  фактическом обновлении.
- Усилить focused tests: точные поверхности 2/11, общая wrapper identity,
  события importer и сохранность полей, чистое завершение stdio с кодом 0,
  фрагментированный и объединённый ввод, а также полный no-write diff таблиц `tasks`,
  `task_runs`, `task_events` для missing/stale guards.
- Открывать read-only Kanban DB через SQLite URI `mode=ro` без `immutable=1`
  и включать `PRAGMA query_only=ON`, чтобы видеть committed uncheckpointed
  данные активной WAL. Запретить Kanban/domain writes,
  initialization/migration helpers и init-lock, но не считать внутреннюю
  SQLite WAL/SHM read coordination доменной мутацией. На quiescent WAL после
  checkpoint и закрытия writer сохранить WAL header, байты и mtime main DB и
  domain state; допустить создание только coordination sidecars, причём
  созданный `-wal` должен быть пустым, а `-shm` допускается.
- Перенести lifecycle hook из всех dependency/triage/blocked веток
  `block_task` в единый post-commit epilogue: при ошибке `COMMIT` транзакция
  полностью откатывается, а внешний hook не вызывается.
- Закрепить детерминированную политику `ByteLineFramer`: fragmented и
  coalesced frames, допустимый остаточный frame на EOF, игнорирование пустых
  строк, protocol validation error для непустого malformed frame без потери
  последующих frames и сохранение clean EOF exit с кодом 0.
- Не добавлять миграцию или изменение схемы БД.
- В отдельном PR добавить один stdlib-only helper
  `scripts/hermes_kanban_mcp_rollout.py` с фазами `prepare`, `switch` и
  `rollback`; каждая фаза по умолчанию только печатает план, а запись
  разрешается только явным `--apply`.
- `prepare` должен проверить полные Git SHA, текущий immutable runtime,
  exact SHA-256 текущего стабильного wrapper, безопасные абсолютные пути и
  создать versioned rollback snapshot и candidate runtime на точном commit.
- `switch` должен повторно проверить snapshot, candidate и ожидаемый текущий
  wrapper SHA-256, затем атомарно заменить только стабильный standalone MCP
  wrapper. `rollback` должен выполнить симметричную проверку и байт-в-байт
  восстановить wrapper из snapshot.
- Добавить только temp-dir automated tests helper; tests не запускают live
  apply, процессы Hermes/Gurra или live MCP process и не читают secrets.

## Возможности

### Новые возможности

- `dedicated-kanban-mcp-surface`: выделенный Kanban MCP server с фиксированной
  read-only/write поверхностью, русскоязычной policy, coalesced stdio
  совместимостью и guarded external sync только в write mode.

### Изменённые возможности

Нет.

## Влияние

- Ожидаемые implementation surfaces:
  `agent/transports/hermes_kanban_mcp_server.py`,
  `agent/transports/hermes_kanban_mcp_stdio.py`,
  `agent/transports/kanban_external_sync_mcp.py`,
  `agent/transports/hermes_tools_mcp_server.py`,
  `hermes_cli/mcp_config.py`, `hermes_cli/subcommands/mcp.py`,
  `hermes_cli/kanban_openspec.py` и `hermes_cli/kanban_db.py`.
- Ожидаемые тестовые поверхности:
  `tests/agent/transports/test_hermes_kanban_mcp_server.py`,
  узкие tests shared MCP boundary,
  `tests/hermes_cli/test_kanban_openspec.py` и
  `tests/hermes_cli/test_kanban_db.py`.
- Новый helper PR затрагивает только
  `scripts/hermes_kanban_mcp_rollout.py` и
  `tests/scripts/test_hermes_kanban_mcp_rollout.py` плюс этот OpenSpec
  change; production modules, DB, services и runtime scripts не меняются.
- Новых зависимостей, обычного Hermes core/model tool и DB migration нет.
- Контракт поверхности остаётся неизменным: ровно 2 read-only и 11 write
  tools.
- История выполненной реализации сохраняется: PR #15 уже слит на
  `062f2f0f1f6947830d1b222a3ef470e145a7c34d`; его выполненные tasks не
  переоткрываются.
- Копия DB или snapshot workaround не создаются; live DB не изменяется.
- Read-only путь не переключает quiescent DB в `DELETE`: после checkpoint и
  закрытия writer исходно отсутствующие `-wal`/`-shm` могут быть созданы
  SQLite только для coordination, при этом main DB bytes/mtime, WAL header,
  `tasks`/`task_runs`/`task_events` и отсутствие init-lock сохраняются, а
  созданный `-wal` имеет нулевой размер и не содержит frames.
- Live connector, Windows config, MCP wrapper, live DB, сервисы и immutable
  runtime `/home/openclaw/staging/hermes-deploy-bbe92d297-20260728` не
  изменяются.
- Rollout остаётся отдельным post-merge gate.
- Создание live candidate/snapshot, atomic wrapper switch, запуск нового MCP
  process, smoke и возможный rollback остаются закрыты отдельным post-helper-PR
  approval gate. Helper PR сам ничего из этого не выполняет.
