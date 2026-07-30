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
`scripts/hermes_kanban_mcp_rollout.py` с командами `bootstrap-prepare`,
`prepare`, `switch` и `rollback`. Каждая команда MUST работать в dry-run mode
по умолчанию и MUST писать только при явном `--apply`. Helper MUST принимать
только явные абсолютные source/runtime/state/wrapper paths, полные
current/candidate Git SHA и ожидаемые SHA-256 evidence; он MUST NOT читать
`.env`, credentials, tokens, sessions, live DB или неуказанный Hermes state.

#### Scenario: Bootstrap dry-run планирует переход из export в baseline

- **WHEN** оператор вызывает `bootstrap-prepare` без `--apply` с exact export
  manifest path, source commit, venv/interpreter и wrapper evidence
- **THEN** helper печатает derived state root, baseline path,
  `bootstrap-<SOURCE_COMMIT>` snapshot ID, observed manifest SHA-256 и wrapper
  хэши `before/after`
- **AND** state root, baseline, snapshot и stable wrapper не создаются и не
  изменяются

#### Scenario: Bootstrap apply создаёт baseline без переключения

- **WHEN** state root отсутствует, его canonical parent существует и все
  evidence совпадают
- **AND** оператор передал manifest SHA-256, одобренный по dry-run plan
- **AND** оператор вызывает `bootstrap-prepare --apply`
- **THEN** helper эксклюзивно создаёт exact state root mode `0700`
- **AND** создаёт detached baseline
  `<state-root>/hermes-kanban-mcp-<SOURCE_COMMIT>` с exact HEAD и clean
  состоянием отслеживаемых файлов
- **AND** копирует только выбранный top-level venv
- **AND** создаёт bootstrap snapshot, но не меняет stable wrapper
- **AND** не запускает процессы, services, network или DB actions

#### Scenario: Bootstrap apply без pinned manifest hash отклоняется

- **WHEN** оператор вызывает `bootstrap-prepare --apply` без
  `--expected-export-manifest-sha256`
- **THEN** helper завершается до создания state root
- **AND** dry-run без этого аргумента остаётся разрешённым для получения
  наблюдаемого хэша

#### Scenario: Экспортный манифест разбирается как строки `key=value`

- **WHEN** `bootstrap-prepare` читает `manifest.txt`, являющийся обычным
  файлом без символьных ссылок строго внутри экспортированной среды
- **AND** файл содержит непустые строки `key=value` в корректной кодировке
  `UTF-8`, уникальные непустые ключи и не содержит `NUL`
- **AND** `source_commit` присутствует ровно один раз, является полным
  `Git SHA` и равен явно переданному `--expected-source-commit`
- **THEN** helper принимает манифест и использует `source_commit` как
  идентичность исходного объекта
- **AND** неизвестные ключи разрешены, но их значения не выводятся и не
  копируются в снимок

#### Scenario: Повреждённый экспортный манифест отклоняется

- **WHEN** `manifest.txt` содержит пустую строку, строку без `=`, пустой или
  повторяющийся ключ, ошибочный `UTF-8`, `NUL`, отсутствующий либо
  несовпадающий `source_commit`
- **THEN** пробный запуск и `--apply` завершаются до любого примитива записи
- **AND** отдельная политика манифеста, общая система конфигурации или
  библиотека схем не создаётся

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
- **THEN** helper отклоняет план до любого примитива записи

### Requirement: Snapshot schema различает export bootstrap и обычный rollout

Bootstrap snapshot MUST использовать `schema_version=2`, закреплять export
manifest path/byte SHA-256 и `source_commit`. Уже существующие ordinary
rollout snapshots schema v2 MUST оставаться readable только для
snapshot-only rollback. Новый ordinary `prepare` MUST создавать
`schema_version=3` с Git before/after runtimes и runtime-coherence evidence;
новый switch-to-target по schema v2 MUST быть запрещён. Обратная совместимость
schema v1 MUST NOT добавляться. Существующий schema v1 artifact MUST закрывать
bootstrap apply без migration или cleanup.

#### Scenario: Bootstrap manifest закрепляет оба runtime identity

- **WHEN** `bootstrap-prepare --apply` завершён во временном дереве
- **THEN** manifest имеет `schema_version=2` и
  `snapshot_kind=bootstrap`
- **AND** before runtime имеет kind `export`, pinned manifest path/hash и
  `source_commit`
- **AND** after runtime имеет kind `git`, exact baseline path и тот же commit
- **AND** wrapper replacement count равен ровно одному

#### Scenario: Обычный prepare создаёт rollout manifest schema v3

- **WHEN** обычный `prepare --apply` строит target из exact Git baseline
- **THEN** manifest имеет `schema_version=3` и `snapshot_kind=rollout`
- **AND** before/after runtimes имеют kind `git` и разные exact commit SHA
- **AND** export manifest fields равны JSON `null`
- **AND** manifest содержит `wrapper_contract=source-cwd-v1` и проверенное
  доказательство происхождения импортов

#### Scenario: Полный consumer проверяет bootstrap switch

- **WHEN** `switch` читает bootstrap snapshot
- **THEN** runtime snapshot validator повторно проверяет export manifest
  хэш/`source_commit`, `venv` экспорта, точный `Git HEAD` базовой среды и `tracked`
  cleanliness и baseline venv
- **AND** общий atomic transition primitive выбирает `wrapper.after`
- **AND** отдельная bootstrap atomic policy не существует

#### Scenario: Snapshot-only rollback не проверяет runtime

- **WHEN** `rollback` читает schema v2 или v3 snapshot
- **THEN** отдельный snapshot-only loader проверяет exact manifest/snapshot
  bytes, modes и hashes, snapshot ID, stable-wrapper path, current-wrapper
  guard и exact `wrapper.before`/`wrapper.after`
- **AND** loader не требует source repo, export manifest, candidate/baseline
  runtime, Git cleanliness, venv, interpreter или imports
- **AND** тот же atomic transition primitive выбирает `wrapper.before`

#### Scenario: Unified root остаётся exact-contained

- **WHEN** baseline и target должны жить в dedicated state root
- **THEN** helper разрешает `runtime-root == state-root`
- **AND** runtime paths выводятся только как
  `hermes-kanban-mcp-<FULL_GIT_SHA>`
- **AND** snapshots выводятся только как `snapshots/<VALID_SNAPSHOT_ID>`
- **AND** разные nested runtime/state roots по-прежнему отклоняются

### Requirement: Rollout runtime coherence доказывает imports из target source

Новые ordinary rollout snapshots MUST использовать `schema_version=3` и
`wrapper_contract=source-cwd-v1`. Новый `wrapper.after` MUST явно выполнять
`cd --` в exact target runtime непосредственно перед `exec`
`<target>/<venv>/bin/python -m hermes_cli.main`, чтобы copied venv
`site-packages` не затенял target checkout. Helper MUST NOT использовать
network, `pip`, editable install или `.pth` files для исправления imports.
Exact wrapper grammar и isolated import-origin policy MUST принадлежать
отдельному `scripts/hermes_kanban_mcp_runtime_coherence.py`; state module MUST
оставаться границей schema/snapshot/transition. Общие path/Git/venv
primitives MUST иметь отдельного единственного common owner; consumers MUST
импортировать их непосредственно, а forwarding re-export façade MUST
отсутствовать. `runtime_coherence.py` MUST содержать не более 900 физических
строк, то есть иметь минимум 100 строк запаса до hard limit 1000.

Перед созданием schema v3 snapshot `prepare --apply` MUST выполнить
import-origin preflight exact candidate interpreter с `-I -S -B` только
внутри OS-level containment, установленного **до** candidate `exec` sealed
captured `bwrap` image. Exact `/usr/bin/bwrap` является allow-listed capture
source и MUST NOT повторно открываться как executable после anchor
construction. Отсутствующий, подменённый или неработоспособный `bwrap` либо
невозможность sealed execution его required loader closure MUST завершать
preflight fail-closed; резервный путь только через Python или повторное
открытие по пути MUST NOT
существовать. Отдельный capability probe MUST считаться только baseline
проверкой executable и базовых namespaces/mounts. Он MUST NOT заявлять
проверку полного candidate-specific профиля. Authoritative проверкой полного
профиля MUST быть реальный production invocation со всем sealed content/data
bundle и exact candidate argv; любая его ошибка MUST завершать операцию
fail-closed.

Sandbox MUST начинаться с пустого mount namespace; получать обычные файлы
кандидата и среды выполнения, точный интерпретатор, необходимую доверенную
стандартную библиотеку, замыкание загрузчика и разделяемых библиотек, а также
`bwrap` только из sealed content bundle;
создавать directory/symlink topology из полного manifest и MUST NOT bind
mutable candidate, `/usr`, `/lib*` или другой backing directory;
предоставлять раздельные tmpfs для `HOME`, `HERMES_HOME` и temp; использовать
`--clearenv` и точный allowlist; создавать свежий `/proc` и минимальный
`/dev`; отделять user/PID/IPC/UTS/cgroup/network namespaces насколько они
поддержаны; использовать `--new-session` и `--die-with-parent`; не
пробрасывать host sockets. Создание daemon, root/deploy/service dependency
MUST NOT требоваться.

Security contract preflight MUST обещать отсутствие host-visible side
effects, а не запрет каждого внутреннего syscall. Candidate MAY попытаться
создать внутренний subprocess либо вызвать native syscall, но mount/PID/
network и остальные namespaces MUST не дать воздействовать на host.
Существующие Python audit/sticky denial и monkeypatch guards MUST оставаться
вторым слоем и диагностическим evidence, но MUST NOT считаться security
boundary.

Во время построения anchors доверенный parent MUST descriptor-relative и с
`O_NOFOLLOW` построить полный манифест топологии каталогов, символических
ссылок и обычных файлов. Каждый исполняемый или импортируемый обычный файл из
дерева
исходного кода кандидата, точного интерпретатора, `pyvenv.cfg`, необходимой
доверенной стандартной библиотеки, замыкания загрузчика и разделяемых
библиотек, доверенного тестового каркаса и `bwrap` MUST быть полностью
прочитан в отдельный memfd/data object, повторно хэширован из него и sealed от
write/grow/shrink/future mutation. Anchors и digests MUST строиться только из
этих sealed captured bytes. Incomplete manifest, unsupported file type,
escape/ambiguous symlink, изменение объекта во время capture или неполный
runtime closure MUST завершать операцию fail-closed.

Контракт MUST обещать exact captured verified bytes от успешного anchor
construction до `exec`/import и MUST NOT обещать защиту исторических bytes до
capture. Bubblewrap MUST получать только sealed regular-file bundle и
созданную из manifest topology; mutable backing directory MUST NOT
bind-монтироваться.

Каждый успешный `open`/`memfd_create` MUST регистрироваться немедленно.
`_data_fd` MUST закрывать current FD при ошибке write/lseek/readback/hash/seal
до handoff. При любой ошибке partial bundle owner MUST закрывать все ранее
приобретённые FDs. Cleanup failure MUST возвращаться как structured
fail-closed error вместе с primary failure и `replacement_applied` state,
MUST NOT скрываться и MUST запрещать snapshot/switch continuation.

Child MUST вернуть evidence, точно совпадающее с manifest/digests sealed
bundle; child self-report MUST NOT быть исходным trust anchor. Любая
symlink/TOCTOU/nested in-place подмена либо forged evidence MUST приводить
только к исполнению sealed captured bytes или к fail-closed. Preflight MUST
определять origin `hermes_cli.main` через
`find_spec` без top-level import, затем импортировать dedicated server после
Python guards и доказать target origins и наличие
`kanban_sync_external_task` в `WRITE_TOOLS`, не отражая arbitrary subprocess
stderr. `switch` MUST повторить точные проверки wrapper/hash/runtime/
import-origin на dry-run и перед atomic replacement. Rollback MUST
восстанавливать exact previous wrapper bytes/mode только по snapshot-owned
guards и MUST NOT запускать candidate либо зависеть от исходного репозитория,
среды выполнения кандидата, виртуального окружения, интерпретатора или
импортов.

#### Scenario: Legacy wrapper превращается в canonical source-cwd wrapper

- **WHEN** ordinary `prepare --apply` строит target из baseline, а текущий
  wrapper является legacy schema v2 wrapper
- **THEN** helper создаёт schema v3 snapshot
- **AND** `wrapper.before` сохраняет exact legacy bytes/mode
- **AND** `wrapper.after` содержит canonical `source-cwd-v1` контракт с
  `cd --` в exact target runtime перед `exec` target interpreter
- **AND** stable wrapper не меняется до отдельного `switch --apply`

#### Scenario: Canonical wrapper остаётся canonical при следующем rollout

- **WHEN** ordinary `prepare --apply` строит следующий target из runtime,
  уже использующего `source-cwd-v1`
- **THEN** helper создаёт детерминированный schema v3 `wrapper.after`
- **AND** новый wrapper сохраняет `cd --` в exact new target runtime перед
  запуском нового target interpreter через `exec`
- **AND** wrapper не содержит path baseline/export как active runtime path

#### Scenario: Затенение старым site-packages отклоняется до snapshot

- **WHEN** copied candidate venv содержит старый installed package в
  `site-packages`
- **AND** запуск без source cwd импортировал бы `hermes_cli.main` или
  `agent.transports.hermes_kanban_mcp_server` из старого package
- **THEN** preflight для schema v3 запускает exact candidate interpreter с
  `-I -S -B` только через exact `/usr/bin/bwrap`, cwd exact target runtime и
  точно разрешённое окружение
- **AND** принимает только module origins внутри exact target checkout
- **AND** отклоняет origin из `venv/lib/python*/site-packages`, рабочего дерева
  baseline/export или любого другого path до создания snapshot
- **AND** ни один `.pth` не исполняется

#### Scenario: Bubblewrap отсутствует или не создаёт containment

- **WHEN** exact `/usr/bin/bwrap` отсутствует, не является ожидаемым
  executable либо baseline capability probe не может создать базовые
  пространства имён `mount`/`PID`/`network`/`user`/`IPC`/`UTS`/`cgroup`
- **THEN** `prepare --apply`, `switch` dry-run и `switch --apply` завершаются
  fail-closed до запуска candidate interpreter
- **AND** Python-only, unsandboxed или частично sandboxed fallback не
  выполняется

#### Scenario: Полный профиль доказывает production invocation

- **WHEN** baseline capability probe успешен
- **AND** полный production invocation не поддерживает либо отклоняет любой
  обязательный `bind` запечатанного содержимого или данных, элемент топологии
  манифеста, флаг пространства имён
  или exact candidate argv
- **THEN** preflight завершается fail-closed
- **AND** baseline probe не переименовывается и не учитывается как
  доказательство полного production profile
- **AND** snapshot или switch replacement не выполняется

#### Scenario: Candidate не получает host-visible side effects

- **WHEN** candidate import graph пытается выполнить direct
  `subprocess._fork_exec`, `ctypes` либо native file/network call, послать
  signal host process или вызвать `resource.prlimit` для host PID
- **THEN** host canary files, sockets, processes, signals и resource limits
  остаются неизменными
- **AND** попытка MAY существовать как внутренний sandbox syscall, но не
  воздействует на host
- **AND** Python audit/sticky denial сохраняет диагностическое evidence и не
  является единственной причиной containment

#### Scenario: Sealed anchors предшествуют candidate exec

- **WHEN** trusted parent готовит preflight
- **THEN** descriptor-relative `O_NOFOLLOW` capture строит полный манифест
  топологии каталогов, символических ссылок и обычных файлов
- **AND** до `exec` кандидата каждый исполняемый или импортируемый обычный файл
  исходного кода кандидата, точного интерпретатора, `pyvenv.cfg`, необходимой
  доверенной стандартной библиотеки и среды выполнения, а также `bwrap`
  материализован в отдельный объект данных `memfd`, повторно хэширован и
  запечатан
- **AND** trusted stdlib roots происходят из trusted parent/system
  interpreter, а не впервые из child self-report
- **AND** anchors/digests построены из тех же sealed captured bytes
- **AND** `bwrap` получает только sealed bundle и созданную из manifest
  topology без bind mutable backing directory
- **AND** child evidence точно совпадает с manifest/digests sealed bundle
- **AND** exact captured verified bytes неизменны от anchor construction до
  `exec`/import

#### Scenario: Подмена interpreter и forged evidence отклоняются

- **WHEN** symlink chain, interpreter, `pyvenv.cfg`, source либо venv
  изменяются во время capture или после него
- **OR** child подделывает resolved path, stdlib roots или digest
- **THEN** preflight использует только sealed captured bytes либо завершается
  fail-closed, и schema v3 snapshot либо switch на tampered bytes не
  выполняется
- **AND** даже успевший запуститься подменённый code остаётся внутри
  OS-level containment и не меняет host canaries

#### Scenario: Nested in-place mutate с matching forged evidence не проходит

- **WHEN** после sealed capture вложенный candidate regular file изменяется
  in-place
- **AND** подменённые bytes пытаются выполнить import/effect и затем
  восстанавливаются
- **AND** child возвращает JSON, полностью совпадающий с expected evidence
- **THEN** sandbox выполняет только sealed original captured bytes либо
  operation завершается fail-closed до import/effect
- **AND** forged matching JSON не заменяет parent sealed-byte anchors
- **AND** host side-effect отсутствует

#### Scenario: Stdlib и executable bytes используют тот же sealed contract

- **WHEN** после capture in-place изменяется required trusted stdlib regular
  file
- **OR** там, где platform даёт reproducible behavioral oracle, изменяются
  exact interpreter или `bwrap` bytes
- **THEN** production invocation использует только sealed captured bytes либо
  завершается fail-closed
- **AND** восстановление backing bytes и matching forged child evidence не
  меняют выбранный content
- **AND** host side-effect отсутствует

#### Scenario: Ошибка acquisition не оставляет FD

- **WHEN** failure injection срабатывает на любом `open`, `memfd_create`,
  чтении исходника, записи `memfd`, `lseek`, повторном чтении/хэшировании,
  запечатывании, передаче манифеста, передаче вызова или последующей проверке
- **THEN** current FD и все ранее зарегистрированные FDs закрываются
- **AND** leaked FDs отсутствуют
- **AND** cleanup failure возвращается как structured fail-closed error
  вместе с primary failure и не скрывается
- **AND** snapshot/switch continuation отсутствует

#### Scenario: WRITE_TOOLS содержит external sync в target runtime

- **WHEN** preflight импортирует dedicated Kanban MCP server из target source
- **THEN** он проверяет exact `WRITE_TOOLS`
- **AND** `kanban_sync_external_task` присутствует в поверхности режима записи
- **AND** отсутствие этого tool завершает `prepare` или `switch` fail-closed до
  примитива записи

#### Scenario: Switch повторяет проверки import-origin

- **WHEN** schema v3 snapshot подготовлен, но target checkout, venv или
  wrapper evidence изменились до `switch` dry-run или `switch --apply`
- **THEN** `switch` в обоих режимах повторно проверяет wrapper SHA, target runtime SHA,
  interpreter evidence, module origins и `WRITE_TOOLS`
- **AND** stale или tampered state отклоняется до atomic replacement

#### Scenario: Prepare dry-run не обещает origin до candidate

- **WHEN** оператор вызывает ordinary `prepare` без `--apply`
- **THEN** plan содержит только deterministic paths, wrapper и hash evidence,
  доступные без создания candidate
- **AND** plan не содержит и не обещает import-origin evidence
- **AND** первое import-origin evidence появляется на `prepare --apply` после
  создания candidate и до snapshot

#### Scenario: Schema v2 snapshot остаётся rollback-compatible

- **WHEN** existing schema v2 snapshot нужен для rollback
- **AND** source repo или candidate missing, corrupt либо dirty
- **THEN** snapshot-only rollback может прочитать snapshot и восстановить exact
  `wrapper.before` bytes/mode по rollback preconditions
- **AND** rollback не требует source repo, candidate runtime/venv/imports или
  исправного состояния package в candidate
- **AND** rollback не запускает `/usr/bin/bwrap`, candidate interpreter или
  предварительную проверку импорта
- **AND** новый switch на target требует schema v3 snapshot вместо schema v2

#### Scenario: Исторический schema-v2 golden независим от production helper

- **WHEN** suite проверяет существующий schema-v2 rollback contract
- **THEN** она читает статические sanitized `manifest.json`,
  `wrapper.before` и `wrapper.after` из исторического snapshot
- **AND** fixture хранит provenance snapshot
  `6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`,
  исходные SHA-256 `83db7f0c4cd2a3239e5d52402f6b8b88e1a66ca46ba1daa5677249fcac4a196f`,
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`,
  `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`
  и исчерпывающий список sanitization substitutions
- **AND** ни `manifest.json`, ни `wrapper.before`, ни `wrapper.after`, ни
  `provenance.json` не содержит raw `/home/openclaw`
- **AND** каждая substitution содержит `file/field`, source class, SHA-256
  source literal, literal replacement, count и reason без raw source value
- **AND** sanitized payload bytes/hashes трёх payload files остаются
  неизменными
- **AND** expected bytes не создаются schema-v3 flow, production wrapper
  generator или production rewrite helper

#### Scenario: Wrapper принимает только exact supported templates

- **WHEN** parser проверяет legacy или canonical wrapper
- **THEN** он принимает только allow-listed template с корректным shebang,
  ожидаемым `set`, exact exports и единственным `exec` с exact argv
- **AND** canonical template требует `cd --` в exact runtime непосредственно
  перед `exec`
- **AND** comments-only совпадение, missing `exec`, extra commands, redirects,
  pipes/backgrounding/command substitution/control operators, лишние argv или
  смешанные runtime/interpreter paths отклоняются до примитивов записи
- **AND** helper не пытается исправлять wrapper через broad rewrite,
  template guessing, delete, `pip` или `.pth`

### Requirement: Helper проверяется только во временном окружении

Automated tests helper MUST находиться в
`tests/scripts/test_hermes_kanban_mcp_rollout.py` и отдельном
`tests/scripts/test_hermes_kanban_mcp_bootstrap.py` и
`tests/scripts/test_hermes_kanban_mcp_runtime_coherence.py` и отдельном
`tests/scripts/test_hermes_kanban_mcp_runtime_sandbox.py`, MUST использовать
только временный Git repo/export/runtime/state/wrapper и MUST запускаться
через `scripts/run_tests.sh`. Общий Git/layout/oracle harness MUST
содержательно принадлежать существующему
`tests/scripts/hermes_kanban_mcp_test_support.py`, а не thin forwarding
façade. Rollout test MUST содержать не более 850 строк, support — менее 400,
behavior MUST остаться неизменным. Runtime-coherence tests
MUST использовать реальные target modules либо faithful fixture, synthetic
HOME и outside-root oracle; sandbox tests MUST проверять host canaries и
bypass attempts. Tests MUST проверять поведение helper вызовом его Python
API/CLI, а не чтением source text. Каждый source/test file MUST содержать
меньше 1000 строк; `runtime_coherence.py` MUST содержать не более 900 строк,
а common path/Git/venv primitives MUST иметь единственного owner без
forwarding re-export façade. Automated suite MUST NOT выполнять live apply, обращаться
к live/staging/Hermes paths, запускать MCP/Hermes/Gurra process, читать
secrets или изменять DB/services.

#### Scenario: Dry-run oracle доказывает отсутствие записи

- **WHEN** temp-only suite выполняет `bootstrap-prepare`, `prepare`, `switch`
  и `rollback` без `--apply`
- **THEN** полный filesystem oracle до/после каждой команды идентичен
- **AND** ни один примитив записи не вызывается

#### Scenario: Apply и rollback проверяются end-to-end во временном дереве

- **WHEN** suite создаёт временный Git repo с current/candidate commits,
  fake venv/interpreter и stable wrapper
- **THEN** prepare создаёт exact candidate/snapshot без switch
- **AND** switch устанавливает exact `wrapper.after`
- **AND** rollback восстанавливает byte-identical `wrapper.before` и mode
- **AND** stale/tampered/hash/path failure cases не изменяют wrapper

#### Scenario: Bootstrap lifecycle проверяется end-to-end

- **WHEN** набор тестов создаёт во временном каталоге экспортированную среду
  вне `Git`, `manifest.txt` с ключами `source_commit`, `deployed_utc`,
  `python_version`, `mcp_version`, `command`, фиктивный `venv`, `wrapper` и
  репозиторий источника с расходящимися коммитами источника и цели
- **THEN** bootstrap создаёт exact baseline/snapshot без switch
- **AND** switch переводит wrapper export→baseline
- **AND** rollback byte-for-byte возвращает export wrapper и mode
- **AND** обычный prepare после bootstrap может построить target из baseline

#### Scenario: Ошибки формата экспортного манифеста проверяются локально

- **WHEN** набор тестов по отдельности создаёт дубликат ключа, повреждённую
  строку, `NUL` или несовпадение `source_commit`
- **THEN** каждый случай отклоняется до записи
- **AND** все входы и результаты остаются только во временном каталоге без
  чтения рабочих путей

#### Scenario: Тесты не используют live targets

- **WHEN** точечные helper tests завершены
- **THEN** все созданные candidate/snapshot/wrapper artifacts находятся под
  временным каталогом теста
- **AND** suite не требует secrets, network, services, processes или live DB

#### Scenario: Repair tests покрывают runtime coherence

- **WHEN** focused repair suite запускается во временном Git/runtime дереве
- **THEN** она покрывает rollout из legacy в canonical, rollout из canonical в
  canonical, совместимость schema v2 rollback, schema v3 switch/rollback,
  rollback при missing/corrupt/dirty candidate, затенение старым
  `site-packages`, dry-run evidence contract и точный список tools
- **AND** она доказывает real HOME isolation, неисполнение `.pth`, запрет
  host-visible network/file/process/DB effects, sanitization stderr и
  проверку отсутствия записи за пределами корня
- **AND** она пытается обойти policy через direct `subprocess._fork_exec`,
  `ctypes`/native write/network, signal и `resource.prlimit`, проверяет
  подмены через символические ссылки/TOCTOU и вложенную последовательность
  изменения на месте → импорт/эффект кандидата → восстановление после
  запечатанного захвата с полностью совпадающими поддельными свидетельствами
  дочернего процесса и отсутствующим или неисправным `bwrap`, после чего все
  контрольные объекты хоста
  остаются неизменными
- **AND** аналогичный behavioral oracle покрывает trusted stdlib regular file
  и, где практично, exact interpreter/`bwrap` bytes
- **AND** failure injection на каждой acquisition/capture/handoff стадии
  подтверждает отсутствие leaked FDs и structured cleanup errors
- **AND** focused suite сохраняет regressions для existing candidate,
  existing snapshot, symlink stable wrapper и future candidate/snapshot
  сценарий с родительской символической ссылкой
- **AND** snapshot-only rollback проходит без запуска candidate и независимо
  от source/venv/interpreter/import state
- **AND** schema-v2 compatibility использует статический sanitized
  historical golden с provenance, исходными SHA-256 и исчерпывающим списком
  substitutions, а не production helper для expected bytes
- **AND** она отклоняет comments-only wrapper, missing `exec`, extra commands,
  redirects и shell control operators
- **AND** все четыре helper test modules проходят, rollout test имеет
  `<=850` строк, reusable support `<400`, каждый source/test file остаётся
  меньше 1000 строк, `runtime_coherence.py` остаётся не больше 900 строк, а
  forwarding façade отсутствует

#### Scenario: Независимая validation имеет write-доступ только к evidence

- **WHEN** независимый reviewer запускает focused four-suite validation
- **THEN** review sandbox имеет `workspace-write`, чтобы runner/tests могли
  писать только temp/cache/evidence
- **AND** review policy остаётся source-read-only и запрещает изменения
  source, tests, fixtures и OpenSpec
- **AND** pre/post source diff не меняется
- **AND** одна exact four-suite команда успешно завершается два раза подряд
- **AND** run без collection, environment blocker либо единственный
  успешный run не закрывает acceptance
- **AND** если support extraction не добавляет test module, exact
  four-module command остаётся неизменной

### Requirement: Изменение не требует DB migration и отделено от live rollout

Forward-port и новый tool MUST использовать существующую схему Kanban DB и
MUST NOT добавлять migration. PR #15 и rollout-helper PR #16 SHALL оставаться
закрытыми delivery steps. Bootstrap-helper SHALL доставляться третьим
отдельным task-owned PR без live effects. Новый state root, immutable
baseline/target, snapshot, точечное переключение MCP wrapper, запуск нового
MCP process и smoke MUST требовать отдельного post-bootstrap-helper-merge
approval.

#### Scenario: Изменения проверяются до PR

- **WHEN** implementation и точечные tests завершены
- **THEN** diff не содержит DB migration, live connector, Windows config,
  live MCP wrapper/config, service или immutable runtime изменений
- **AND** diff не добавляет зависимостей
- **AND** новый independent review принят без `BLOCK`
- **AND** только после accepted review разрешены commit, push и task-owned PR

#### Scenario: OS-sandbox approval не открывает delivery или live gate

- **WHEN** пользователь явно одобряет material delta после третьего `BLOCK`
  формулировкой «material OS-sandbox delta без live rollout»
- **THEN** разрешаются implementation и repo-local/temp-only verification
- **AND** accepted independent review и delivery tasks остаются pending
- **AND** planning approval не разрешает commit, push, PR или live-действия

#### Scenario: Новый remediation baseline требует нового approval

- **WHEN** независимый запуск
  `20260729T192126Z-kanban-os-sandbox-independent-review` возвращает `BLOCK`
- **THEN** tasks 19.4–19.7 переоткрываются, а 16.7, 18.8, 19.8 и 19.9
  остаются открытыми
- **AND** набор доверенных дескрипторов, выделение общего владельца,
  provenance sanitization, возвращённые regressions и probe contract
  оформляются отдельными незавершёнными remediation tasks
- **AND** предыдущее OS-sandbox approval не разрешает их implementation
- **AND** перед implementation требуется новое явное approval

#### Scenario: Sealed-content baseline требует ещё одного approval

- **WHEN** независимый запуск
  `20260729T224514Z-kanban-remediation-independent-review` два раза успешно
  выполняет точную команду для четырёх наборов тестов, но возвращает `BLOCK`
- **THEN** зелёные runs сохраняются как evidence и не закрывают acceptance
- **AND** tasks 19.4, 19.6, 19.7 и 20.2 вместе со связанными completion
  claims переоткрываются
- **AND** 16.7, 18.8, 19.8, 19.9, 20.7 и 20.8 остаются открытыми
- **AND** запечатанный пакет содержимого, безопасное при исключениях управление
  ресурсами, повторно используемый тестовый каркас и новые состязательные тесты
  оформляются в отдельном разделе 21.x
- **AND** до нового явного approval запрещены implementation,
  scripts/tests/fixtures changes и implementation test runs

#### Scenario: Reviewer-only source deviation не является acceptance

- **WHEN** reviewer временно изменил source для probe, затем побайтово
  восстановил его и pre/post fingerprints совпали
- **THEN** deviation фиксируется честно, но исключается из mandatory evidence
- **AND** он не считается implementation change
- **AND** следующий independent review остаётся source-read-only, получая
  `workspace-write` только для temp/cache/evidence

#### Scenario: Bootstrap-helper PR не выполняет rollout

- **WHEN** bootstrap capability, schema v2 и temp-only tests реализованы и
  проверены
- **THEN** diff не содержит production module/script/test changes вне exact
  областей helper/test/OpenSpec
- **AND** действующий корень состояния, базовая среда, целевая среда, снимок, переключение `wrapper`,
  process и smoke не выполняются
- **AND** bootstrap-helper PR является отдельным delivery step после PR #16

#### Scenario: Доставка после слияния остаётся закрыта

- **WHEN** bootstrap-helper PR слит
- **THEN** state root, baseline, target и snapshot ещё не создаются
- **AND** `bootstrap-prepare --apply`, обычный `prepare --apply`, wrapper
  switch, process или smoke ещё не выполняются без отдельного одобрения exact
  `dry-run`-плана
- **AND** глобальный Hermes symlink, Hermes/Gurra restart и dirty Telegram
  patch остаются вне scope

#### Scenario: Live scope требует отдельного exact разрешения

- **WHEN** OS-sandbox implementation проверена, review принят и PR слит
- **THEN** live rollout, wrapper replacement, restart, process replacement и
  DB actions всё ещё запрещены
- **AND** каждое такое действие требует отдельного exact разрешения и не
  выводится из planning approval, review, commit, push, PR или merge

### Requirement: Sealed acquisition имеет предварительный bounded inventory

Система SHALL до создания content memfd выполнить descriptor-relative
bounded inventory с `O_NOFOLLOW`, построить topology, identities, digests и
exact ELF dependency plan, затем проверить resource budgets. Только после
успешных проверок система SHALL вторым проходом захватить sealed bytes и
повторно сверить topology, identity и digest. Любое расхождение MUST
завершать операцию fail-closed до invocation со structured cleanup.

#### Scenario: Budget failure предшествует partial acquisition

- **WHEN** inventory требует больше FD или serialized arguments, чем
  разрешает рассчитанный budget
- **THEN** система возвращает structured fail-closed error до создания
  первого content memfd и до invocation

#### Scenario: Canonical invocation одинаков для budget и execution

- **GIVEN** bounded inventory содержит directories, regular files, symlinks,
  permissions и exact ELF closure
- **WHEN** parent планирует probe и production до sealed acquisition
- **THEN** один immutable canonical spec MUST включать полную topology,
  harness/anchors, loader/preload argv и все FD roles
- **AND** symbolic render MUST использовать worst-case legal decimal width из
  finite `RLIMIT_NOFILE`
- **AND** actual render того же spec MUST быть не больше prevalidated bound и
  повторно проходить args/exec/current+peak FD/pass_fds checks перед
  соответствующим args memfd/subprocess
- **AND** directory/symlink-heavy cap failure происходит до первого content
  memfd
- **AND** отдельный acquisition temporary reserve MUST учитывать
  `MAX_DIRECTORY_DEPTH + 1` одновременно удерживаемых directory FD, source FD
  и создаваемый sealed memfd

#### Scenario: Exact role order является canonical contract

- **GIVEN** canonical inventory и ELF closure
- **WHEN** строится probe role map
- **THEN** exact order MUST быть `loader`, `library:0..N`, `bwrap`,
  `probe_args`
- **WHEN** строится production role map
- **THEN** exact order MUST быть ordered `file:<destination>`, `harness`,
  `anchors`, `loader`, `library:0..N`, `bwrap`, `production_args`
- **AND** missing, extra или reordered role map MUST завершаться
  `ResourceBudgetError` до render и subprocess

#### Scenario: Объект изменён между проходами

- **WHEN** topology, identity или digest объекта во втором проходе отличается
  от inventory
- **THEN** система не использует изменённые bytes, закрывает все приобретённые
  ресурсы и завершает операцию fail-closed

#### Scenario: Topology углублена между inventory и acquisition

- **GIVEN** approved `InventoryPlan` построен для topology в пределах
  canonical `MAX_DIRECTORY_DEPTH`
- **WHEN** до acquisition добавлена ветка глубже этого предела, а в snapshot
  существует лексически более ранний regular file
- **THEN** повторный canonical inventory preflight MUST завершиться
  structured fail-closed до первого content memfd
- **AND** все временные descriptor resources MUST быть закрыты

#### Scenario: Mutation после topology preflight ограничена acquisition guard

- **WHEN** topology становится глубже `MAX_DIRECTORY_DEPTH` после успешного
  preflight, но до обхода изменённой ветки
- **THEN** acquisition traversal MUST применить тот же canonical предел
  независимо
- **AND** ошибка MUST произойти до открытия directory, source либо content
  memfd на запрещённой глубине, без превышения temporary FD bound и без leak

### Requirement: ELF closure следует exact GNU/Linux loader semantics

Система SHALL раздельно хранить `DT_RPATH` и `DT_RUNPATH`, моделировать их
GNU/Linux precedence и inheritance, включая superseding `RUNPATH` для
defining object и legacy inheritance `RPATH`. `$ORIGIN`, `$LIB` и
`$PLATFORM` SHALL раскрываться только детерминированно из exact
runtime/platform либо MUST быть отклонены до capture. Relative, empty,
unsafe и escaping entries MUST отклоняться. Dynamic segment MUST быть
bounded, кратен entry size и содержать `DT_NULL` внутри segment; string
offsets и terminators MUST быть bounded. `DT_NEEDED` MUST быть safe soname
без slash, `NUL` и escape.

#### Scenario: External ELF symlink не покидает trusted roots

- **GIVEN** initial external ELF path находится внутри injectable trusted root
- **WHEN** любой absolute или relative symlink hop направляет оставшийся path
  наружу, в dangling target либо cycle
- **THEN** inventory MUST завершиться fail-closed после этого hop
- **AND** production roots `/usr`, `/lib`, `/lib64` не изменяются тестом

#### Scenario: RUNPATH и RPATH дают разные closure

- **WHEN** вручную созданная ELF-фикстура различает прямой `RUNPATH` и
  унаследованный устаревший `RPATH`
- **THEN** dependency plan совпадает с независимым GNU/Linux oracle и не
  объединяет эти поля

#### Scenario: Malformed dynamic metadata отклонена

- **WHEN** dynamic segment имеет некратный размер, не содержит bounded
  `DT_NULL`, ссылается за string table либо содержит unsafe `DT_NEEDED`
- **THEN** система завершает plan fail-closed до sealed acquisition

#### Scenario: Неразрешимый token не уходит в defaults

- **WHEN** `$LIB`, `$PLATFORM` или иной token нельзя точно определить из
  среды выполнения и платформы
- **THEN** система отклоняет closure до capture и не ищет dependency в
  каталогах по умолчанию

### Requirement: Resource planner доказывает вместимость полного invocation

Система SHALL до sealed acquisition и invocation детерминированно считать
current open FDs, все content entries, manifest, loader, `bwrap`, libraries,
harness, anchors, probe/prod args FDs и явный subprocess/`bwrap` reserve и
сравнивать сумму с finite `RLIMIT_NOFILE`. Система SHALL считать bytes
полного exec `argv` и environment относительно `SC_ARG_MAX` с именованным
safety margin. Serialized payload `bwrap --args` SHALL иметь отдельный явный
maximum и SHALL проверяться до memfd/invocation с учётом platform,
`pass_fds` и `bwrap` constraints.

#### Scenario: Низкий FD limit или занятые FD

- **WHEN** текущие open FDs и полный рассчитанный набор с reserve не
  помещаются в finite `RLIMIT_NOFILE`
- **THEN** система fail-closed до partial sealed acquisition и subprocess

#### Scenario: Late FD pressure блокирует final handoff

- **WHEN** дополнительное FD pressure возникает после ранней проверки, но
  после создания args/harness/anchors memfd и до subprocess
- **THEN** authoritative final check MUST проверить exact final `pass_fds`,
  uniqueness, open state, current count и subprocess/bwrap peak reserve
- **AND** structured fail-closed error возвращается без вызова subprocess

#### Scenario: Превышен argv или args payload

- **WHEN** exec `argv` + environment превышает `SC_ARG_MAX` за вычетом margin
  либо serialized `bwrap --args` превышает отдельный maximum
- **THEN** система fail-closed до создания args/content memfd и invocation

#### Scenario: Cleanup не маскирует primary failure

- **WHEN** после acquisition failure дополнительный cleanup также завершается
  ошибкой
- **THEN** structured result сохраняет primary cause и отдельно сообщает
  cleanup failure без FD leak и continuation
