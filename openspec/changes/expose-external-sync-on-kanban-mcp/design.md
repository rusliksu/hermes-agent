## Контекст

Актуальная опорная вершина `main` репозитория `rusliksu/hermes-agent`:
`6a8f6802ce8eed7f6c7db4481568ac7605058ba9`. Локальные `HEAD` и
`origin/main` проверены и идентичны на `6a8f6802`.

PR #12 добавил guarded `kanban_sync_external_task` в
`agent/transports/hermes_tools_mcp_server.py`. Фактический Windows connector
запускает другой entry point:
`hermes_cli.main mcp serve-kanban --allow-write`, который импортирует
`agent.transports.hermes_kanban_mcp_server`. Финальный adapter blob до
расхождения старой ветки зафиксирован commit
`db0c7d7e484f96137fb1371bd9667d8029d6aa05`; в актуальном `main` этого adapter
нет.

Происхождение частей для точечного переноса:

- `4b4a07d25`: выделенный Kanban MCP server и базовые tests;
- `82c3597f8`: CLI `serve-kanban` wiring и stdio exposure;
- `4cd6b4318`: OpenSpec task import и совместимые Kanban DB части;
- `db1af8ebd`: обработка coalesced stdio messages;
- `db0c7d7e4`: русскоязычная policy и русские status labels.

Старая ветка содержит постороннюю и устаревшую историю, поэтому целиком
переносить её нельзя.

Предыдущее независимое ревью заблокировало реализацию из-за смешения
ответственностей adapter, SQL/private API в importer, transport coupling и
недостаточных stdio/no-write проверок. Эти замечания были оформлены
предыдущим architecture delta.

Финальное ревью повторно вынесло `BLOCK` по трём load-bearing остаткам.
Во-первых, активная Kanban DB работает в WAL и меняется параллельно, поэтому
read-only URI с `immutable=1` может игнорировать uncheckpointed WAL и вернуть
устаревшие данные. Во-вторых, dependency/triage/blocked ветки `block_task`
вызывают lifecycle hook внутри `write_txn` до `COMMIT`, из-за чего внешний
эффект возможен после последующего rollback. В-третьих, framer contract не
закрепляет остаточный valid frame на EOF, пустые и несколько frames, а также
продолжение после malformed nonblank frame. Этот material delta заменяет
соответствующие решения; его точный WAL-контракт повторно явно одобрен.

## Цели / вне целей

**Цели:**

- вернуть на актуальный `main` фактическую выделенную Kanban MCP поверхность;
- сохранить существующие 10 инструментов и их mode gating;
- сохранить read-only отсутствие DB initialization/write side effects;
- сохранить русскоязычную policy и additive русские status labels;
- сохранить coalesced stdio behavior;
- добавить защищённую синхронизацию внешних задач как одиннадцатый инструмент
  только в режиме записи;
- использовать одну нейтральную общую MCP-обёртку в обоих серверах;
- отделить framing/lifecycle stdio от allow-listed адаптера и удержать адаптер
  меньше 1000 строк;
- сделать `kanban_db` единственным владельцем атомарного OpenSpec source
  upsert и canonical audit events;
- читать active WAL через `mode=ro` без `immutable=1`, не выполняя
  изменений состояния Kanban-домена;
- вызывать lifecycle hook `block_task` только после успешного `COMMIT`;
- закрепить детерминированную edge policy чистого `ByteLineFramer`;
- подтвердить поведение целевыми unit-тестами и реальным stdio
  `list_tools` smoke, включая фрагментированный/объединённый ввод и чистое
  завершение по EOF.

**Вне целей:**

- merge или cherry-pick старой adapter ветки целиком;
- новая sync-логика, title/fuzzy/source-path lookup или batch sync MCP tool;
- смешивание OpenSpec source-definition persistence с существующим terminal
  `sync_external_task`;
- новый обычный Hermes model/core tool;
- новая зависимость или DB migration;
- копирование DB, snapshot workaround или запись в live DB;
- изменение live connector, Windows config, wrapper, live DB или сервисов;
- изменение immutable runtime
  `/home/openclaw/staging/hermes-deploy-bbe92d297-20260728`;
- глобальный `/home/openclaw/.hermes/hermes-agent` symlink, restart
  Hermes/Gurra или перенос dirty Telegram patch.

## Решения

### 1. Узкий ручной перенос с контролем происхождения

Implementation SHALL восстанавливать только итоговые Kanban MCP artifacts и
CLI wiring из пяти focused commits, начиная с финального adapter blob
`db0c7d7e4` и вручную согласуя его с API актуального `main`. Старую ветку
нельзя merge, rebase или cherry-pick целиком.

Ожидаемый минимальный набор:

- добавить `agent/transports/hermes_kanban_mcp_server.py`;
- добавить нейтральный
  `agent/transports/hermes_kanban_mcp_stdio.py`;
- добавить нейтральный
  `agent/transports/kanban_external_sync_mcp.py`;
- добавить `hermes_cli/kanban_openspec.py`;
- добавить `tests/agent/transports/test_hermes_kanban_mcp_server.py`;
- добавить `tests/hermes_cli/test_kanban_openspec.py`;
- точечно добавить `serve-kanban` parser/dispatcher wiring в
  `hermes_cli/subcommands/mcp.py` и `hermes_cli/mcp_config.py`;
- точечно добавить публичный OpenSpec source-definition API и tests в
  `hermes_cli/kanban_db.py` и `tests/hermes_cli/test_kanban_db.py`;
- минимально изменить `agent/transports/hermes_tools_mcp_server.py` только для
  импорта или re-export shared wrapper symbols.

Альтернатива: cherry-pick пяти commits. Она отвергнута, потому что commits
основаны на старом дереве и могут вернуть устаревшие соседние строки или
конфликтующие DB изменения.

### 2. Точный набор инструментов является контрактом безопасности

Read-only mode регистрирует ровно:

1. `kanban_board_status`;
2. `kanban_list_tasks`.

Write mode регистрирует эти два инструмента и девять write tools:

1. `kanban_enqueue`;
2. `kanban_claim_next`;
3. `kanban_heartbeat`;
4. `kanban_complete`;
5. `kanban_block`;
6. `kanban_add_dependency`;
7. `kanban_reclaim`;
8. `kanban_import_openspec_tasks`;
9. `kanban_sync_external_task`.

Первые 10 инструментов должны сохранить прежние имена и gating. Exact-list
tests здесь не являются snapshot изменяемого каталога: узкая allow-list
выделенного MCP adapter является намеренной границей доступа.

### 3. Синхронизация внешних задач принадлежит нейтральной общей границе MCP

`KANBAN_EXTERNAL_SYNC_TOOL` и `kanban_sync_external_task` SHALL принадлежать
новому нейтральному модулю
`agent.transports.kanban_external_sync_mcp`. И выделенный Kanban adapter, и
`hermes_tools_mcp_server` импортируют одну и ту же реализацию из этого модуля.
`hermes_tools_mcp_server` MAY re-export прежние symbols для обратной
совместимости, но не владеет второй реализацией.

Общая обёртка:

- требует явный `dry_run` на уровне Python/MCP signature;
- отклоняет `dry_run=false` без непустого `expected_current_status` до
  изменения БД;
- вызывает только `hermes_cli.kanban_db.sync_external_task`;
- использует exact `external_key` или explicit `task_id`, но не title lookup;
- возвращает сериализованный `ExternalTaskSyncResult`.

Оба servers регистрируют этот exact wrapper без копирования guards, SQL или
тонкой transport-specific sync функции. Identity/делегирование общей
реализации является тестируемым контрактом.

Альтернатива: импортировать wrapper из `hermes_tools_mcp_server` или
скопировать его в новый server. Она отвергнута, потому что связывает два
transport adapter и создаёт риск расхождения guards.

### 4. OpenSpec importer не владеет сохранением данных

`hermes_cli.kanban_db` SHALL предоставить узкий публичный batch API
`upsert_openspec_task_definitions`. Это отдельная source-definition операция,
не alias и не расширение существующих terminal
`sync_external_task`/`sync_external_tasks`.

`kanban_openspec` SHALL только разобрать `tasks.md`, сформировать список
definition specs и одним вызовом передать его в публичный API. Importer не
выполняет SQL, не вызывает `_new_task_id` или иной private DB API и не пишет
events.

Публичный API выполняет весь batch атомарно и сопоставляет строки только по
exact `external_key`:

- новая задача получает `status=todo`, `created_by=openspec`, а также
  source-owned `external_key`, `source_path`, `title` и `body`;
- существующая задача меняет только `source_path`, `title` и `body`;
- поля `status`, `assignee`, сведения о владельце claim, `current_run_id`,
  `result`, workflow/runtime поля и все прочие не source-owned значения сохраняются;
- создание пишет canonical event `created`;
- фактическое обновление пишет один canonical sync/edit event
  `external_synced` с изменёнными source-owned fields;
- unchanged definition не обновляет строку и не пишет event.

API не выполняет checkbox-to-status transition, не завершает и не
переоткрывает задачи. Существующая схема и exact-key unique index достаточны;
migration не добавляется.

Альтернатива: оставить SQL в importer или использовать terminal
`sync_external_tasks`. Первая нарушает ownership и audit contract; вторая
смешивает source-definition import с terminal sync semantics и допускает
другой набор изменяемых полей.

### 5. Read-only доступ является WAL-aware без доменных записей

Dedicated read handlers SHALL открывать существующую Kanban DB через SQLite
URI `mode=ro` без `immutable=1` и после соединения устанавливать
`PRAGMA query_only=ON`. Так SQLite учитывает active WAL и видит
зафиксированные, но ещё не checkpointed строки.

Read path MUST NOT выполнять Kanban/domain writes, вызывать initialization или
migration helpers, менять task/run/event rows либо брать init-lock. Копия DB
или snapshot workaround не создаются, и никаких writes в live DB не
выполняется.

Логические записи Kanban отличаются от внутренней read coordination SQLite.
Если `-wal` и `-shm` уже существуют у активного writer, SQLite MAY
использовать их для locks/read marks; это не является Kanban mutation.
Поэтому implementation и tests не обещают неизменность mtime существующего
`-shm`.

Отдельная quiescent проверка сохраняет режим WAL. После
`wal_checkpoint(TRUNCATE)` и закрытия writer `-wal`/`-shm` исходно
отсутствуют. Read-only handler MAY создать пустой `-wal` и coordination
`-shm`; это допустимо только при неизменных bytes/mtime main DB, domain state
`tasks`/`task_runs`/`task_events`, WAL header и отсутствии init-lock.
Созданный `-wal` обязан иметь нулевой размер, то есть не содержать frames.
Переключение `journal_mode=DELETE` не используется.

Active-WAL test держит writer в WAL mode с `wal_autocheckpoint=0`, фиксирует
строку, оставшуюся uncheckpointed, и подтверждает, что MCP read видит её.
После чтения bytes/mtime main DB, WAL content и domain state остаются теми
же, reader не создаёт init-lock или domain rows, а coordination существующего
SHM допускается.

Альтернатива: использовать `immutable=1` или читать копию/snapshot.
`immutable=1` может возвращать stale data при active WAL; копия вводит
отдельный источник истины.

### 6. Lifecycle hook `block_task` вызывается только после `COMMIT`

Во всех dependency, triage и already-blocked ветках `block_task` SHALL выйти
из `write_txn` до вызова `_fire_kanban_lifecycle_hook`. Внутри транзакции не
должно быть раннего hook или `return`; результат для hook формируется внутри
ветки, а единый post-commit epilogue вызывается только после успешного выхода
из transaction context.

При ошибке `COMMIT` изменения `tasks`, `task_runs` и `task_events` полностью
откатываются, а lifecycle hook не вызывается. На успешном пути hook через
отдельное read-only соединение должен видеть durable committed status.

Альтернатива: оставить hook перед выходом из `write_txn`. Она отвергнута,
потому что внешний наблюдатель может увидеть событие, которому не
соответствует durable состояние после commit failure.

### 7. Framing и lifecycle stdio отделены от allow-listed адаптера

`agent/transports/hermes_kanban_mcp_server.py` владеет только allow-listed
handlers, FastMCP registration, server instructions и mode gating.
`agent/transports/hermes_kanban_mcp_stdio.py` владеет custom stdio byte-buffer,
newline-delimited JSON-RPC framing, обработкой partial reads и lifecycle до
корректного завершения при EOF.

После extraction adapter MUST содержать меньше 1000 строк. Дальнейшее
изменение, которое доводит его до 1000 строк или выше, запрещено без новой
extraction; framing/lifecycle нельзя возвращать в adapter.

Чистый `ByteLineFramer` детерминированно сохраняет неполный хвост байтов между
чтениями и извлекает каждое полное newline-delimited сообщение отдельно. Его
unit matrix покрывает fragmented frame, несколько coalesced frames и
остаточный valid frame без завершающего newline на EOF. Пустые newline frames
игнорируются, не теряя соседние valid JSON-RPC frames. Непустой malformed
frame передаётся в protocol validation как ошибка, после чего последующие
frames остаются доступными и не теряются.

После допустимого запроса и EOF сервер завершает процесс самостоятельно с
кодом возврата 0; тест не использует `terminate()` или `kill()` для успешного
пути.

Альтернатива: вернуться к SDK `run_stdio_async()`. Она отвергнута, потому что
focused evidence фиксирует stall на subprocess pipes в этом runtime.

### 8. Политика русского языка сохраняется как инструкция, а не regex-гейт

`SERVER_INSTRUCTIONS`, описания human-facing OpenSpec import и additive
`status_label` сохраняют русскую policy из `db0c7d7e4`. Technical identifiers,
API names, code, paths, tool names, library names, README и internal status
codes могут оставаться на английском внутри русского текста. Кириллический
regex enforcement не добавляется.

### 9. Проверка выполняется только на изолированной временной DB

Focused tests запускаются через `scripts/run_tests.sh`. Они обязаны покрыть:

- точные tool lists: ровно 2 в read-only и 11 в write mode;
- наличие прежних 10 tools и отсутствие sync в read-only;
- использование обоими серверами идентичной общей обёртки;
- exact external-key mapping без title lookup;
- dry-run без task/run/event записей;
- rejection mutating вызова без guard и со stale status с полным сравнением
  таблиц `tasks`, `task_runs`, `task_events`;
- WAL-aware чтение committed uncheckpointed строки при
  `wal_autocheckpoint=0`, неизменность main DB bytes/mtime, отсутствие domain
  writes/init-lock и допустимость coordination существующего SHM;
- quiescent WAL после checkpoint и закрытия writer: исходное отсутствие
  sidecars, неизменность main DB bytes/mtime, domain state и WAL header,
  отсутствие init-lock, допустимый coordination `-shm` и нулевой размер
  созданного `-wal`;
- post-commit lifecycle success с наблюдением durable status через отдельное
  read-only соединение и commit-failure rollback без вызова hook;
- OpenSpec create/update/unchanged audit events и сохранение running/done
  runtime ownership/result/workflow полей;
- прямой вызов CLI `serve-kanban --allow-write`;
- чистые `ByteLineFramer` tests для fragmented, coalesced, residual EOF,
  blank и malformed nonblank frames без потери последующих valid frames;
- реальный stdio `list_tools` smoke и самостоятельное чистое завершение по
  EOF с кодом возврата 0;
- line-count gate: adapter меньше 1000 строк и stdio ownership остаётся в
  нейтральном модуле.

Тестовое окружение задаёт временные `HERMES_HOME`, `HOME` и
`HERMES_KANBAN_DB`; live DB path должен быть явно исключён.

## Риски / компромиссы

- [Старый blob перезаписывает свежий main] → переносить по файлам и функциям,
  затем проверять diff против `6a8f6802`, без cherry-pick ветки.
- [Один из прежних 10 tools теряется или попадает не в тот mode] → exact-list
  tests для обоих режимов и stdio smoke.
- [Sync guard расходится между MCP servers] → общая нейтральная обёртка и
  identity tests; не копировать SQL или guard logic.
- [Importer обходит canonical DB audit] → публичный атомарный
  `upsert_openspec_task_definitions`, запрет SQL/private API в importer и
  тесты событий и сохранения полей.
- [Adapter снова становится god-file] → hard gate `<1000`, явное ownership
  разделение и новая extraction до дальнейшего роста.
- [Read-only probe с `immutable=1` возвращает stale active-WAL data] →
  всегда открывать обычный `mode=ro` без `immutable=1`, включить `query_only`
  и проверить committed uncheckpointed row при `wal_autocheckpoint=0`.
- [Read-only проверка ошибочно принимает SQLite coordination за доменную
  запись] → отдельно сравнивать main DB bytes/mtime, WAL content,
  tasks/runs/events/init-lock; не обещать неизменность mtime существующего
  SHM, а на quiescent WAL проверять исходное отсутствие sidecars и только
  coordination-эффект: пустой созданный `-wal`, допустимый `-shm`, неизменный
  WAL header и отсутствие domain mutation.
- [Lifecycle hook создаёт внешний эффект до durable commit] → единый
  post-commit epilogue, commit-failure injection с полным rollback и нулём
  вызовов hook, success read из отдельного соединения.
- [Stdio теряет residual, соседний или следующий после malformed frame] →
  чистая детерминированная framer matrix для fragmented/coalesced/EOF/blank/
  malformed случаев плюс bounded subprocess clean-EOF smoke.
- [Тест касается live DB] → temp env, явное сравнение путей и запрет live
  констант.
- [Merge не обновляет активный connector] → rollout вынесен в отдельный
  post-merge approval и новый immutable runtime.

## План доставки

1. Зафиксировать полученное повторное явное одобрение material delta после
   ревью `BLOCK`.
2. Перед возобновлением implementation повторно сверить task branch с authoritative
   `origin/main`; при изменении base оформить material delta.
3. Переделать незакоммиченную реализацию по ownership boundaries, публичному
   DB API, WAL-aware read-only, post-commit lifecycle и усиленным framer
   контрактам.
4. Запустить focused tests, line-count/file containment checks, language
   check, strict OpenSpec validation и проверить diff на отсутствие
   migration/dependency/deploy/live изменений.
5. Получить новое независимое ревью без `BLOCK`.
6. Только после зелёного ревью создать отдельный task-owned PR; до merge
   никаких live действий.
7. После merge запросить отдельное одобрение на новый immutable runtime из
   merge SHA и точечное переключение только MCP wrapper/нового MCP process.
8. Не менять глобальный Hermes symlink и не перезапускать Hermes/Gurra.

Будущий rollback, если отдельный rollout будет одобрен, ограничивается
возвратом MCP wrapper на предыдущий immutable target и перезапуском только
нового MCP process. Конкретный rollback target фиксируется в отдельном
пакете доставки.

## Открытые вопросы

Блокирующих архитектурных вопросов нет. Material delta явно одобрен;
публикация и rollout остаются закрыты следующими отдельными гейтами.
