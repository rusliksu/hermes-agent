## ADDED Requirements

### Requirement: Выделенный Kanban MCP server доступен через CLI

Hermes SHALL предоставлять entry point `hermes mcp serve-kanban`, который
запускает выделенный stdio MCP server
`agent.transports.hermes_kanban_mcp_server`. Флаг `--allow-write` MUST явно
управлять регистрацией write tools.

#### Scenario: Запуск без write-флага

- **WHEN** пользователь запускает `hermes mcp serve-kanban`
- **THEN** CLI передаёт adapter режим read-only
- **AND** write tools не регистрируются

#### Scenario: Запуск с write-флагом

- **WHEN** пользователь запускает `hermes mcp serve-kanban --allow-write`
- **THEN** CLI передаёт adapter режим write
- **AND** разрешённая write surface регистрируется

### Requirement: Набор инструментов имеет точный контракт режимов

Read-only mode MUST содержать ровно `kanban_board_status` и
`kanban_list_tasks`. Write mode MUST сохранять эти два инструмента и прежние
восемь write tools `kanban_enqueue`, `kanban_claim_next`,
`kanban_heartbeat`, `kanban_complete`, `kanban_block`,
`kanban_add_dependency`, `kanban_reclaim`,
`kanban_import_openspec_tasks`, а также новый
`kanban_sync_external_task`.

#### Scenario: Точный read-only список

- **WHEN** adapter строит tool surface с `allow_write=false`
- **THEN** `list_tools` возвращает ровно два read-only инструмента
- **AND** ни один write tool, включая `kanban_sync_external_task`, не виден

#### Scenario: Точный write список

- **WHEN** adapter строит tool surface с `allow_write=true`
- **THEN** `list_tools` возвращает ровно 11 перечисленных инструментов
- **AND** все 10 прежних инструментов сохраняются
- **AND** `kanban_sync_external_task` является одиннадцатым write-only
  инструментом

### Requirement: Режим только чтения не изменяет Kanban DB

Handlers режима только чтения MUST открывать существующую Kanban DB без
инициализации через SQLite URI `mode=ro` и MUST устанавливать
`PRAGMA query_only=ON`. URI MUST NOT использовать `immutable=1`, чтобы reader
видел committed uncheckpointed данные active WAL. Handlers MUST NOT выполнять
Kanban/domain writes, вызывать initialization или migration helpers, изменять
`tasks`, `task_runs`, `task_events` либо создавать init-lock. При уже активной
WAL SQLite MAY использовать существующие WAL/SHM для внутренней read
coordination; это не является Kanban mutation, и неизменность mtime
существующего SHM не гарантируется. Для quiescent WAL после checkpoint и
закрытия writer SQLite MAY создать отсутствующие coordination sidecars
`-wal`/`-shm`; main DB bytes/mtime, WAL header и domain state MUST оставаться
неизменными, init-lock MUST отсутствовать, а созданный `-wal` MUST иметь
нулевой размер и не содержать frames.

#### Scenario: Чтение quiescent WAL создаёт только coordination sidecars

- **WHEN** read-only MCP client вызывает status и list на временной
  существующей quiescent DB в режиме WAL
- **AND** checkpoint завершён, writer закрыт и `-wal`/`-shm` исходно
  отсутствуют
- **THEN** adapter возвращает безопасные metadata и task metadata
- **AND** main DB bytes и mtime не меняются
- **AND** DB header остаётся в режиме WAL
- **AND** `tasks`, `task_runs`, `task_events` не добавляются и не изменяются
- **AND** init-lock не создаётся
- **AND** SQLite MAY создать только `-wal`/`-shm` для coordination
- **AND** созданный `-wal` имеет нулевой размер и не содержит frames
- **AND** наличие coordination `-shm` допускается

#### Scenario: Чтение видит зафиксированную строку активной WAL

- **WHEN** writer держит временную DB в WAL mode с `wal_autocheckpoint=0`
- **AND** committed row остаётся в WAL без checkpoint
- **THEN** read-only MCP status или list видит эту строку
- **AND** reader не изменяет bytes/mtime main DB, domain state или WAL content
- **AND** reader не создаёт init-lock или domain rows
- **AND** внутренняя coordination через существующий SHM допускается

#### Scenario: Запрещённые write helpers

- **WHEN** read-only handlers выполняются с test doubles, запрещающими
  write-capable domain connection, `init_db`, migration и другие write/init
  helpers
- **THEN** handlers завершаются без вызова этих helpers

#### Scenario: Read-only путь не использует копию или snapshot

- **WHEN** dedicated handler открывает Kanban DB для чтения
- **THEN** он читает исходную DB через `mode=ro` и `query_only`
- **AND** не использует `immutable=1`
- **AND** копия DB или snapshot workaround не создаются

### Requirement: Синхронизация внешних задач использует общую границу MCP и принятые проверки-защиты

`kanban_sync_external_task` MUST переиспользовать существующий
`hermes_cli.kanban_db.sync_external_task` через одну реализацию
`KANBAN_EXTERNAL_SYNC_TOOL` и `kanban_sync_external_task` в нейтральном
`agent.transports.kanban_external_sync_mcp`. Выделенный Kanban server и
`hermes_tools_mcp_server` MUST импортировать эту общую реализацию;
`hermes_tools_mcp_server` MAY re-export symbols для обратной совместимости.
Guards, SQL и wrapper logic MUST NOT дублироваться. Инструмент MUST требовать
explicit boolean `dry_run`; при `dry_run=false` он MUST требовать непустой
`expected_current_status` до любой DB mutation. Сопоставление MUST выполняться
по точному `external_key` или explicit `task_id`, но MUST NOT выполнять title
lookup.

#### Scenario: Оба сервера используют идентичную обёртку

- **WHEN** тест загружает external sync symbols обоих MCP servers
- **THEN** обе server surface делегируют одной реализации из нейтрального
  общего модуля границы
- **AND** transport modules не содержат копий guards или SQL

#### Scenario: Одинаковый title не выбирает unrelated task

- **WHEN** unrelated Kanban task имеет тот же title, что входящая external task
- **AND** sync вызван с новым точным `external_key`
- **THEN** unrelated task не изменяется
- **AND** результат относится только к exact-key mapping

#### Scenario: Предварительный просмотр не пишет данные

- **WHEN** write-mode client вызывает sync с `dry_run=true`
- **THEN** инструмент возвращает planned sync result
- **AND** task, task run и task event rows не добавляются и не изменяются

#### Scenario: Запись без ожидаемого статуса отклоняется

- **WHEN** write-mode client вызывает sync с `dry_run=false` без
  `expected_current_status`
- **THEN** вызов отклоняется до DB mutation

#### Scenario: Устаревший ожидаемый статус отклоняется

- **WHEN** `expected_current_status` не совпадает с текущим статусом
  exact-key задачи
- **THEN** существующий sync guard возвращает ошибку
- **AND** полные снимки таблиц `tasks`, `task_runs` и `task_events` остаются
  неизменными

#### Scenario: Отсутствующий ожидаемый статус не пишет ни в одну таблицу

- **WHEN** mutating sync вызван без непустого `expected_current_status`
- **THEN** вызов отклоняется до DB mutation
- **AND** полные снимки таблиц `tasks`, `task_runs` и `task_events` остаются
  неизменными

### Requirement: Определения источника OpenSpec сохраняются каноническим API Kanban DB

`hermes_cli.kanban_db` SHALL предоставлять узкий публичный batch API
`upsert_openspec_task_definitions`, отдельный от terminal
`sync_external_task`. OpenSpec parser/importer MUST только формировать
definition specs и вызывать этот API; importer MUST NOT выполнять SQL или
использовать private DB API. Весь batch MUST быть атомарным и MUST
сопоставляться только по exact `external_key`.

#### Scenario: Новое определение OpenSpec создаёт todo с событием аудита

- **WHEN** batch содержит отсутствующий exact `external_key`
- **THEN** API создаёт задачу со `status=todo`, `created_by=openspec` и
  переданными `external_key`, `source_path`, `title`, `body`
- **AND** API пишет canonical `created` event
- **AND** migration или checkbox-to-status transition не выполняется

#### Scenario: Существующая running или done задача сохраняет runtime-владение

- **WHEN** exact-key задача уже существует в `running` или `done`
- **AND** OpenSpec definition меняет `source_path`, `title` или `body`
- **THEN** API изменяет только фактически отличающиеся source-owned fields
- **AND** сохраняет status, assignee, claim, current run, result,
  workflow/runtime поля и прочие не source-owned значения
- **AND** пишет один canonical `external_synced` event с изменёнными полями

#### Scenario: Неизменившееся определение не пишет данные или событие

- **WHEN** exact-key задача уже содержит те же `source_path`, `title` и `body`
- **THEN** API не обновляет строку задачи
- **AND** новый task event не создаётся

#### Scenario: Откат пакета сохраняет атомарность

- **WHEN** любая definition в batch не может быть сохранена
- **THEN** ни одна task row или task event этого batch не фиксируется

### Requirement: Адаптер сохраняет политику русского языка

MCP server instructions SHALL требовать русский язык для human-facing Kanban
title, body, comment, block reason, result, acceptance criteria и
human-readable OpenSpec artifacts/tasks. Technical identifiers и internal
status codes MAY оставаться на английском; regex-проверка Кириллицы MUST NOT
добавляться.

#### Scenario: Клиент получает policy при initialize

- **WHEN** MCP client инициализирует выделенный server
- **THEN** server instructions содержат требование писать human-facing текст
  по-русски
- **AND** инструкции явно допускают английские technical identifiers

#### Scenario: Русский status label является additive

- **WHEN** adapter возвращает известный internal status
- **THEN** исходное status value сохраняется
- **AND** отдельное `status_label` содержит русский label

### Requirement: Lifecycle блокировки запускается только после успешного COMMIT

Во всех dependency, triage и blocked ветках `block_task` MUST завершить
`write_txn` до вызова `_fire_kanban_lifecycle_hook`. Внутри транзакции MUST
NOT быть раннего lifecycle hook или возврата, обходящего единый post-commit
epilogue. Hook MUST вызываться только после успешного `COMMIT`.

#### Scenario: Ошибка COMMIT полностью откатывает блокировку

- **WHEN** failure injection вызывает ошибку на `COMMIT` любой dependency,
  triage или blocked ветки `block_task`
- **THEN** изменения `tasks`, `task_runs` и `task_events` полностью
  откатываются
- **AND** lifecycle hook не вызывается

#### Scenario: Успешный hook видит устойчиво зафиксированный status

- **WHEN** `block_task` успешно фиксирует изменение и вызывает lifecycle hook
- **THEN** hook запускается после выхода из `write_txn`
- **AND** hook через отдельное read-only соединение видит зафиксированный
  устойчивый status

#### Scenario: Все ветки используют единый post-commit epilogue

- **WHEN** выполняется любая dependency, triage или already-blocked ветка
  `block_task`
- **THEN** внутри транзакции формируется результат без раннего hook или
  `return`
- **AND** единый epilogue обрабатывает lifecycle только после успешного
  `COMMIT`

### Requirement: Транспорт stdio обрабатывает фрагментированные и объединённые сообщения и чистый EOF

Нейтральный `agent.transports.hermes_kanban_mcp_stdio` MUST владеть byte
buffer, newline-delimited JSON-RPC framing и process lifecycle. Он MUST
предоставлять чистый детерминированный `ByteLineFramer`, сохранять неполный
tail между чтениями, разбирать каждое полное сообщение, выдавать остаточный
непустой frame при EOF и после valid request и EOF самостоятельно завершаться
с return code 0. Пустые newline frames MUST игнорироваться. Непустой malformed
frame MUST оставаться protocol validation error и MUST NOT приводить к потере
последующих frames.

#### Scenario: Уведомление об инициализации и список инструментов пришли одним блоком

- **WHEN** client записывает `notifications/initialized` и `tools/list` одной
  объединённой записью в stdin
- **THEN** server разбирает оба сообщения отдельно
- **AND** `tools/list` возвращается в пределах bounded timeout

#### Scenario: Одно сообщение разделено между чтениями

- **WHEN** client отправляет одно valid newline-delimited JSON-RPC сообщение
  несколькими фрагментированными записями
- **THEN** server сохраняет неполные bytes до получения delimiter
- **AND** обрабатывает сообщение ровно один раз после его завершения

#### Scenario: Остаточный valid frame выдаётся на EOF

- **WHEN** последний valid JSON-RPC frame не имеет завершающего newline
- **AND** input завершается EOF
- **THEN** `ByteLineFramer` выдаёт остаточный frame ровно один раз

#### Scenario: Пустые frames не теряют соседние сообщения

- **WHEN** между двумя valid JSON-RPC frames находятся одна или несколько
  пустых newline frames
- **THEN** пустые frames игнорируются
- **AND** оба valid frames выдаются в исходном порядке

#### Scenario: Malformed frame не теряет последующие frames

- **WHEN** непустой malformed frame предшествует valid JSON-RPC frame в одном
  или нескольких чтениях
- **THEN** malformed frame приводит к protocol validation error
- **AND** последующий valid frame остаётся доступным и обрабатывается без
  потери

#### Scenario: Допустимый EOF завершает процесс чисто

- **WHEN** client получает ответ на valid request и закрывает stdin
- **THEN** server самостоятельно завершается в пределах bounded timeout
- **AND** process return code равен 0
- **AND** тест не вызывает `terminate()` или `kill()` на успешном пути

### Requirement: Файловая ответственность удерживает адаптер ниже жёсткого лимита

`agent.transports.hermes_kanban_mcp_server` MUST владеть allow-listed
handlers, FastMCP registration, instructions и mode gating, но MUST NOT
владеть stdio byte-buffer/framing/lifecycle. После extraction adapter MUST
содержать меньше 1000 строк. Дальнейший рост до 1000 строк или выше MUST быть
запрещён без новой extraction.

#### Scenario: Границы ответственности проверяются после реализации

- **WHEN** выполняется validation файловой архитектуры
- **THEN** adapter содержит ровно ответственность allow-listed
  handlers/registration и меньше 1000 строк
- **AND** stdio byte-buffer/framing/lifecycle находится в нейтральном stdio
  модуле
- **AND** новая transport-specific копия shared sync wrapper отсутствует

### Requirement: Standalone rollout управляется одним fail-closed helper

Репозиторий SHALL предоставлять один stdlib-only helper
`scripts/hermes_kanban_mcp_rollout.py` с командами `prepare`, `switch` и
`rollback`. Каждая команда MUST работать в dry-run mode по умолчанию и MUST
писать только при явном `--apply`. Helper MUST принимать только явные
абсолютные source/runtime/state/wrapper paths, полные current/candidate Git
SHA и ожидаемый текущий wrapper SHA-256; он MUST NOT читать `.env`,
credentials, tokens, sessions, live DB или Hermes state.

#### Scenario: Prepare dry-run только печатает полный план

- **WHEN** оператор вызывает `prepare` без `--apply` с валидными paths и hashes
- **THEN** helper печатает candidate path, snapshot ID, wrapper before/after
  hashes и все планируемые операции
- **AND** filesystem, Git worktrees и stable wrapper остаются byte-for-byte
  неизменными

#### Scenario: Prepare apply создаёт exact candidate и rollback snapshot

- **WHEN** оператор вызывает `prepare --apply` с полным candidate Git SHA,
  exact expected current runtime SHA и current wrapper SHA-256
- **THEN** helper создаёт detached candidate
  `<runtime-root>/hermes-kanban-mcp-<FULL_GIT_SHA>` с exact `HEAD`
- **AND** переносит только явно выбранный top-level `.venv` или `venv`, но не
  runtime state, secrets, config, DB, sessions или dirty tracked patch
- **AND** создаёт exclusive versioned snapshot с `manifest.json`,
  `wrapper.before` и `wrapper.after`
- **AND** stable wrapper ещё не изменяется

#### Scenario: Switch atomically меняет только stable wrapper

- **WHEN** подготовленный snapshot/candidate не изменены
- **AND** `switch --apply` получает SHA-256, совпадающий с текущим wrapper и
  `wrapper_before_sha256` manifest
- **THEN** helper повторно проверяет paths, exact Git SHA, venv и оба snapshot
  hashes
- **AND** заменяет stable wrapper одним same-filesystem `os.replace` после
  синхронизации файла через `fsync`
- **AND** новый wrapper byte-for-byte совпадает с `wrapper.after`
- **AND** helper не запускает и не останавливает процессы и не пишет DB

#### Scenario: Rollback восстанавливает точный предыдущий wrapper

- **WHEN** текущий wrapper SHA-256 совпадает с явным guard и
  `wrapper_after_sha256` manifest
- **AND** оператор вызывает `rollback --apply` для exact snapshot ID
- **THEN** helper атомарно восстанавливает bytes и executable mode
  `wrapper.before`
- **AND** candidate и snapshot сохраняются
- **AND** никакой process restart или smoke автоматически не выполняется

#### Scenario: Stale или tampered state блокирует запись

- **WHEN** расходится expected wrapper SHA-256, current/candidate Git SHA,
  manifest, snapshot hash, venv/interpreter или canonical path
- **THEN** `prepare`, `switch` или `rollback` завершается fail-closed до
  изменения stable wrapper
- **AND** helper не пытается исправлять состояние через delete, `rm`,
  `rmtree`, `git reset`, `git clean` или wildcard cleanup

#### Scenario: Path escape и symlink target отклоняются

- **WHEN** managed root, candidate, snapshot или stable wrapper использует
  relative path, `..`, symlink, broad target, path вне явно разрешённого root
  либо неожиданный существующий candidate
- **THEN** helper отклоняет план до любого write primitive

### Requirement: Helper проверяется только во временном окружении

Automated tests helper MUST находиться в
`tests/scripts/test_hermes_kanban_mcp_rollout.py`, MUST использовать только
временный Git repo/runtime/state/wrapper и MUST запускаться через
`scripts/run_tests.sh`. Tests MUST проверять поведение helper вызовом его
Python API/CLI, а не чтением source text. Automated suite MUST NOT выполнять
live apply, обращаться к live/staging/Hermes paths, запускать MCP/Hermes/Gurra
process, читать secrets или изменять DB/services.

#### Scenario: Dry-run oracle доказывает отсутствие записи

- **WHEN** temp-only suite выполняет `prepare`, `switch` и `rollback` без
  `--apply`
- **THEN** полный filesystem oracle до/после каждой команды идентичен
- **AND** ни один write primitive не вызывается

#### Scenario: Apply и rollback проверяются end-to-end во временном дереве

- **WHEN** suite создаёт временный Git repo с current/candidate commits,
  fake venv/interpreter и stable wrapper
- **THEN** prepare создаёт exact candidate/snapshot без switch
- **AND** switch устанавливает exact `wrapper.after`
- **AND** rollback восстанавливает byte-identical `wrapper.before` и mode
- **AND** stale/tampered/hash/path failure cases не изменяют wrapper

#### Scenario: Тесты не используют live targets

- **WHEN** focused helper tests завершены
- **THEN** все созданные candidate/snapshot/wrapper artifacts находятся под
  временным каталогом теста
- **AND** suite не требует secrets, network, services, processes или live DB

### Requirement: Изменение не требует DB migration и отделено от live rollout

Forward-port и новый tool MUST использовать существующую схему Kanban DB и
MUST NOT добавлять migration. Implementation SHALL доставляться сначала
отдельным task-owned PR. Rollout helper SHALL доставляться вторым отдельным
task-owned PR без live effects. Новый immutable runtime, snapshot, точечное
переключение MCP wrapper, запуск нового MCP process и smoke MUST требовать
отдельного post-helper-merge approval.

#### Scenario: Изменения проверяются до PR

- **WHEN** implementation и focused tests завершены
- **THEN** diff не содержит DB migration, live connector, Windows config,
  live MCP wrapper/config, service или immutable runtime изменений
- **AND** diff не добавляет зависимостей
- **AND** task-owned PR является первым delivery step

#### Scenario: Helper PR не выполняет rollout

- **WHEN** helper и temp-only tests реализованы и проверены
- **THEN** diff не содержит production module/script/test changes вне exact
  областей helper/test/OpenSpec
- **AND** live candidate, snapshot, wrapper switch, process и smoke не
  выполняются
- **AND** helper PR является отдельным delivery step после PR #15

#### Scenario: Доставка после слияния остаётся закрыта

- **WHEN** PR #15 и helper PR слиты
- **THEN** новый immutable runtime ещё не создаётся
- **AND** snapshot, MCP wrapper, process или smoke ещё не выполняются без
  отдельного одобрения exact dry-run plan
- **AND** глобальный Hermes symlink, Hermes/Gurra restart и dirty Telegram
  patch остаются вне scope
