## 1. Гейты перед реализацией

- [x] 1.1 Получить явное одобрение planning baseline до любых code changes.
- [x] 1.2 Повторить `git fetch origin main`, подтвердить authoritative base и
  при изменении SHA оформить material OpenSpec delta до продолжения.
- [x] 1.3 Получить повторное явное одобрение material architecture delta после
  независимого ревью `BLOCK`; до этого не продолжать реализацию и не
  публиковать текущие незакоммиченные изменения.
- [x] 1.4 Получить повторное явное одобрение material delta по WAL-aware
  read-only, включая точный quiescent WAL coordination oracle, post-commit
  lifecycle и framer edge policy после финального ревью `BLOCK`; до этого не
  продолжать реализацию или публикацию.

## 2. Происхождение и границы ответственности выделенного адаптера

- [x] 2.1 Сопоставить focused commits `4b4a07d25`, `82c3597f8`,
  `4cd6b4318`, `db1af8ebd`, `db0c7d7e4` с актуальным `main` и выписать только
  относящиеся к Kanban MCP hunks.
- [x] 2.2 Вынести custom stdio byte-buffer, newline framing и lifecycle в
  нейтральный `agent/transports/hermes_kanban_mcp_stdio.py`; оставить adapter
  владельцем только allow-listed handlers, FastMCP registration, instructions
  и mode gating.
- [x] 2.3 Сократить `hermes_kanban_mcp_server.py` до менее 1000 строк и
  добавить containment gate, запрещающий рост до 1000 строк или выше без
  новой extraction.
- [x] 2.4 Повторно проверить точечный CLI parser/dispatcher wiring для
  `hermes mcp serve-kanban [--allow-write]`.
- [x] 2.5 Перевести dedicated read handlers на WAL-aware SQLite URI
  `mode=ro` без `immutable=1` с `PRAGMA query_only=ON`, чтобы читать active
  WAL; исключить Kanban/domain writes, init/migration helpers и init-lock,
  сохранив русскоязычную policy и additive status labels.

## 3. Общая граница MCP внешней синхронизации

- [x] 3.1 Перенести `KANBAN_EXTERNAL_SYNC_TOOL` и
  `kanban_sync_external_task` в нейтральный
  `agent/transports/kanban_external_sync_mcp.py`.
- [x] 3.2 Перевести оба MCP server на одну общую реализацию обёртки;
  сохранить допустимый re-export из `hermes_tools_mcp_server` для обратной
  совместимости и не дублировать guards или SQL.
- [x] 3.3 Зарегистрировать общий `kanban_sync_external_task` только в write
  mode выделенного adapter, сохранив exact 2/11 tool surfaces, explicit
  `dry_run`, expected-status guard и отсутствие title lookup.

## 4. Каноническое сохранение определений OpenSpec

- [x] 4.1 Добавить в `hermes_cli.kanban_db` публичный атомарный batch API
  `upsert_openspec_task_definitions` с exact-key create/update/unchanged
  semantics, canonical `created`/`external_synced` events и без migration.
- [x] 4.2 Удалить SQL и private DB API из OpenSpec importer: parser формирует
  definition specs и вызывает только публичный batch API.
- [x] 4.3 Подтвердить, что create задаёт `todo`, `created_by=openspec` и
  source fields, update меняет только source-owned fields, а status, assignee,
  claim/current run, result и workflow/runtime поля сохраняются без
  автоматического переноса checkbox в status.

## 5. Усиленные целевые тесты

- [x] 5.1 Проверить exact-list contract: ровно два read-only и 11 write-mode
  tools, сохранение прежних 10 и отсутствие sync в режиме только чтения.
- [x] 5.2 Проверить, что оба MCP server используют идентичную общую обёртку
  и не содержат копий guards или SQL.
- [x] 5.3 Проверить события create/update/unchanged importer, откат пакета и
  сохранение running/done status, assignee, claim/current run, result и
  workflow/runtime полей.
- [x] 5.4 Добавить реальный stdio smoke: одно фрагментированное сообщение
  JSON-RPC, объединённые сообщения, ограниченный timeout и чистое завершение
  по EOF с кодом возврата 0 без
  `terminate()`/`kill()`.
- [x] 5.5 Усилить dry-run, missing guard и stale-status no-write tests полным
  сравнением таблиц `tasks`, `task_runs`, `task_events`.
- [x] 5.6 Запустить affected Python tests только через
  `scripts/run_tests.sh` и подтвердить отсутствие обращений к live DB.
- [x] 5.7 Добавить active-WAL test с `wal_autocheckpoint=0`: committed
  uncheckpointed row видна MCP reader, main DB bytes/mtime, WAL content и
  domain state не меняются, init-lock/domain rows не создаются, coordination
  существующего SHM допускается. На quiescent WAL после checkpoint и закрытия
  writer исходно подтвердить отсутствие `-wal`/`-shm`, а после read-only
  handler — неизменность main DB bytes/mtime, `tasks`/`task_runs`/`task_events`
  и WAL header, отсутствие init-lock, допустимый coordination `-shm` и
  нулевой размер созданного `-wal` без frames; не переключать
  `journal_mode=DELETE`.
- [x] 5.8 Добавить tests всех dependency/triage/blocked веток `block_task`:
  единый post-commit hook, полный rollback `tasks`/`task_runs`/`task_events` и
  отсутствие hook при injected `COMMIT` failure, а на success — видимость
  durable status через отдельное read-only соединение hook.
- [x] 5.9 Добавить чистые детерминированные tests `ByteLineFramer` для
  fragmented frame, нескольких coalesced frames, residual valid frame на EOF,
  игнорирования blank frames без потери соседних valid frames и protocol
  validation error malformed nonblank frame без потери последующих frames;
  сохранить clean EOF exit 0.

## 6. Проверки, независимое ревью и отдельный PR задачи

- [x] 6.1 После повторной реализации выполнить русскоязычную OpenSpec
  проверку `Test-OpenSpecRussian.ps1` или строгий эквивалент на VPS, затем
  выполнить `openspec validate expose-external-sync-on-kanban-mcp --strict
  --no-interactive`.
- [x] 6.2 Повторно проверить adapter line count `<1000`, файловое ownership и diff на
  отсутствие посторонних изменений старой ветки, миграций БД, новых
  зависимостей, правок live connector/MCP wrapper, Windows config, сервисов и
  артефактов среды выполнения.
- [x] 6.3 Получить новое независимое ревью реализации и закрыть все
  замечания уровня `BLOCK` до публикации.
- [x] 6.4 После зелёных проверок и независимого ревью создать отдельный
  task-owned PR; не выполнять
  deploy, symlink switch, restart или live process changes.

## 7. Отдельный гейт доставки после слияния

- [ ] 7.1 После merge отдельно запросить одобрение на новый immutable runtime
  из merge SHA и зафиксировать rollback target.
- [ ] 7.2 Только после отдельного одобрения точечно переключить MCP wrapper и
  запустить новый MCP process; не менять глобальный Hermes symlink, не
  перезапускать Hermes/Gurra и не переносить dirty Telegram patch.
