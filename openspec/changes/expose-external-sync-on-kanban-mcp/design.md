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

После закрытия этих замечаний реализация была слита как PR #15 на exact
коммите `062f2f0f1f6947830d1b222a3ef470e145a7c34d`. Защищённый вспомогательный инструмент
`prepare/switch/rollback` затем был слит отдельным PR #16 на exact commit
`9fcd66651768e3cf220d5cd501efbec5ae3e2550`. Выполненные sections 1–10
`tasks.md` сохраняются закрытыми. Live rollout после merge не выполнялся.

Новый material delta основан только на явно предоставленном evidence; live
пути в planning run повторно не читались. Evidence фиксирует:

- export runtime `/home/openclaw/.hermes/mcp/hermes-kanban` не является Git
  worktree;
- экспортный манифест
  `/home/openclaw/.hermes/mcp/hermes-kanban/manifest.txt` использует формат
  строк `key=value` в `UTF-8` и содержит
  `source_commit=6f8738dc308f909bf1735883344f2fcc12f3cbcd`;
- candidate main равен
  `9fcd66651768e3cf220d5cd501efbec5ae3e2550`; source/candidate histories не
  находятся в ancestor relation, merge-base равен
  `9de9c25f620ff7f1ce0fd5457d596052d5159596`;
- wrapper SHA-256 равен
  `20e2cb13c7162a833fea32f79aea59591e759c4ca2ab181e0c0a12f0e3add089` и
  содержит ровно одну ссылку на export runtime;
- venv dirname равен `venv`, interpreter SHA-256 равен
  `1643dacd9feaedc58f3cc581e4d22577dfe25c09b10282936186ccf0f2e61118`;
- выделенный каталог `/home/openclaw/.hermes/mcp-rollout-state`, вычисленная базовая среда
  `hermes-kanban-mcp-6f873...` и target `hermes-kanban-mcp-9fcd...`
  отсутствуют.

Обычный `prepare` из PR #16 намеренно требует current runtime как exact clean
Git worktree. Поэтому он не должен ослаблять этот guard для export layout.
Нужен отдельный bootstrap contract, который сначала создаёт immutable exact
Git baseline, не заменяя export in-place и не меняя stable wrapper.

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
- добавить один минимальный stdlib Python helper для подготовки exact-SHA
  standalone candidate, versioned rollback snapshot, атомарного переключения
  стабильного MCP wrapper и его точного восстановления;
- сделать `prepare`, `switch` и `rollback` dry-run-only по умолчанию, с
  отдельным явным `--apply` и stale-wrapper SHA-256 guard в каждой пишущей
  фазе;
- проверить helper только во временных Git/runtime деревьях без live process,
  DB, wrapper, service или secret access.
- добавить одну dry-run-first команду `bootstrap-prepare`, которая создаёт
  exact Git baseline из non-Git export evidence до обычного `prepare`;
- сохранить одну общую schema/validation/atomic transition policy для
  bootstrap и обычных snapshots;
- удержать каждый source/test file ниже 1000 строк через одну реальную
  ownership extraction, а не thin wrappers.

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
- автоматический restart/start/stop, process discovery, MCP smoke, DB probe,
  изменение connector/Windows config или любое действие над live/staging
  target в helper PR;
- общий deployment framework, новый CLI Hermes, новая библиотека,
  dependency installer, package builder или поддержка произвольных layout;
- перенос незакоммиченных tracked файлов, untracked runtime state, `.env`,
  credentials, tokens, sessions или иных secret/state файлов в candidate.
- замена export runtime in-place, его превращение в Git worktree или удаление;
- ancestry/rebase/merge между source commit и candidate main;
- live state root, baseline, snapshot, prepare, switch, process или smoke в
  bootstrap-helper PR.

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

### 10. PR #16: один helper и три явные фазы являются rollout baseline

Новый файл SHALL быть ровно
`scripts/hermes_kanban_mcp_rollout.py`. Он использует только Python stdlib и
уже обязательный для source checkout executable `git`; новый package,
Hermes subcommand, reusable deployment abstraction или shell wrapper не
добавляются.

CLI contract:

```text
python scripts/hermes_kanban_mcp_rollout.py prepare \
  --source-repo ABS_PATH \
  --runtime-root ABS_PATH \
  --state-root ABS_PATH \
  --current-runtime ABS_PATH \
  --expected-current-runtime-sha FULL_GIT_SHA \
  --candidate-sha FULL_GIT_SHA \
  --venv-dirname .venv|venv \
  --stable-wrapper ABS_PATH \
  --expected-current-wrapper-sha256 FULL_SHA256 \
  [--apply]

python scripts/hermes_kanban_mcp_rollout.py switch \
  --runtime-root ABS_PATH \
  --state-root ABS_PATH \
  --snapshot-id SNAPSHOT_ID \
  --stable-wrapper ABS_PATH \
  --expected-current-wrapper-sha256 FULL_SHA256 \
  [--apply]

python scripts/hermes_kanban_mcp_rollout.py rollback \
  --runtime-root ABS_PATH \
  --state-root ABS_PATH \
  --snapshot-id SNAPSHOT_ID \
  --stable-wrapper ABS_PATH \
  --expected-current-wrapper-sha256 FULL_SHA256 \
  [--apply]
```

Отсутствие `--apply` всегда означает dry-run. Отдельный `--dry-run` не нужен:
это единственный default mode. `--apply` не принимается вместе с
неизвестными аргументами, не запрашивает интерактивное подтверждение и не
расширяет область путей.

`prepare --apply`:

1. повторяет все read-only preconditions;
2. создаёт candidate как detached Git worktree на полном
   `candidate-sha` по производному пути
   `<runtime-root>/hermes-kanban-mcp-<FULL_GIT_SHA>`;
3. копирует только указанный top-level `.venv` или `venv` из чистого
   текущего immutable runtime в candidate, не копируя `.env`, config, DB,
   sessions, patches или другие runtime files;
4. строит candidate wrapper заменой точного абсолютного
   `current-runtime` на точный candidate path в существующем стабильном
   wrapper, сохраняя остальной byte content и executable mode;
5. создаёт immutable rollback snapshot и завершает работу без переключения
   стабильного wrapper.

Трансформация wrapper разрешена только если он является обычным
несимвольным executable UTF-8 файлом, содержит текущий runtime path хотя бы
один раз, не содержит candidate path и содержит запускаемый контракт
`mcp serve-kanban` с `--allow-write`. Иначе helper завершается fail-closed.
Это сохраняет существующий standalone launcher без нового шаблонизатора.

`switch --apply` повторно проверяет snapshot, оба exact Git SHA, candidate
venv/interpreter, byte hashes `wrapper.before`/`wrapper.after`, путь wrapper
и переданный expected current wrapper SHA-256. Только после этого он пишет
same-directory temp file, выполняет file `fsync`, сохраняет executable mode,
делает один `os.replace(temp, stable_wrapper)` и `fsync` каталога.
При любой ошибке до успешного `os.replace` helper в `finally` удаляет только
созданный этим вызовом exact temp path; cleanup failure не скрывает первичную
ошибку и выдаёт безопасное предупреждение без broad cleanup. После успешного
`os.replace` состояние считается применённым: ошибка directory `fsync` или
post-install verification завершается с кодом 2, но печатает однозначный JSON
stderr с `replacement_applied=true`, ожидаемым installed SHA-256 и действием
`inspect/rollback`. Процессы и DB helper не трогает.

`rollback --apply` требует, чтобы текущий wrapper SHA-256 совпадал и с явным
guard, и с `wrapper_after_sha256` manifest. После полной повторной проверки
он тем же атомарным primitive восстанавливает точные bytes и mode
`wrapper.before`. Candidate и snapshot не удаляются.

Альтернатива: один `rollout --apply`, объединяющий prepare и switch. Она
отвергнута, потому что не оставляет оператору проверяемой паузы между
созданием candidate/snapshot и live cutover. Альтернатива: shell script.
Она отвергнута из-за более сложной закрывающей проверки путей,
`JSON`-манифеста снимка, хэширования и модульных тестов только во временной
среде.

### 11. PR #16: schema v1 фиксирует обычный Git-to-Git rollout

Snapshot ID SHALL детерминированно иметь вид
`<CURRENT_FULL_SHA>-to-<CANDIDATE_FULL_SHA>` и создаваться эксклюзивно под
`<state-root>/snapshots/<snapshot-id>`. Поэтому dry-run и последующий apply
формируют один exact path без скрытого clock input. Повторное использование
существующего ID запрещено. Snapshot содержит только:

- `manifest.json` с `schema_version=1`, UTC timestamp, всеми нормализованными
  путями, полными current/candidate Git SHA, wrapper before/after SHA-256,
  mode, candidate path, venv dirname и числом точных замен runtime path;
- `wrapper.before` с исходными bytes;
- `wrapper.after` с candidate bytes.

Manifest не содержит environment, token, credential, DB data или wrapper
text. Snapshot files создаются с owner-only permissions и после записи
проверяются повторным hash. Текущий runtime не копируется: он уже immutable,
его полный SHA и путь зафиксированы, а rollback snapshot сохраняет exact
wrapper, который на него указывает. Ни prepare, ни rollback не удаляют
runtime/snapshot и не используют `rm`, `rmtree`, `git reset`, `git clean`,
wildcards или cleanup globs.

Candidate считается готовым только если его `HEAD` равен полному
`candidate-sha`, tracked status чист, ожидаемый venv/interpreter существует,
а путь находится строго внутри `runtime-root`. Текущий runtime аналогично
должен иметь exact expected `HEAD` и чистые tracked files; это исключает
перенос dirty Telegram patch. Создание candidate из уже существующего пути
запрещено: helper не пытается исправлять, очищать или перезаписывать
сомнительное состояние. Ошибка после частичного создания candidate, venv или
snapshot сохраняет evidence только в exact производных candidate/snapshot
paths, не запускает автоматический cleanup и заставляет повторный `prepare`
завершиться fail-closed на существующем candidate или snapshot.

Альтернатива: полный tar/copy snapshot текущего runtime. Она отвергнута как
лишняя и потенциально захватывающая secrets/state; immutable current runtime
плюс exact SHA и byte-identical wrapper дают достаточный rollback oracle.

### 12. PR #16: path validation и test oracle закрывают опасные края

Все пути должны быть абсолютными, без `..`, NUL и shell expansion. Managed
roots, candidate, snapshot и stable wrapper не могут быть symlink; existing
ancestors разрешаются через `resolve(strict=True)` и обязаны оставаться под
явно переданным root. Helper отклоняет `/`, home directory и source repo root
в качестве managed runtime/state target, одинаковые/вложенные друг в друга
state/runtime roots и любой candidate или snapshot вне производного exact
path. Stable wrapper допускается только как ровно указанный существующий
regular file; helper не ищет его по имени.

`prepare`, `switch` и `rollback` сначала строят полный JSON plan в памяти и
проверяют все доступные preconditions. Dry-run печатает этот plan и
завершается, не вызывая `mkdir`, `copytree`, `git worktree add`, temp-file
creation или `os.replace`.

Automated tests живут только в
`tests/scripts/test_hermes_kanban_mcp_rollout.py`, создают локальный
временный Git repo с двумя commits, fake venv/interpreter, wrapper,
runtime-root и state-root. Основной oracle:

- полный snapshot временного filesystem до/после каждого dry-run идентичен;
- wrapper hash/mode неизменны при любом precondition/path/hash failure;
- successful prepare создаёт exact candidate и три snapshot files с
  согласованными hashes, но не меняет stable wrapper;
- successful switch меняет только stable wrapper на exact
  `wrapper.after`;
- rollback dry-run ничего не меняет, а rollback apply восстанавливает
  byte-identical `wrapper.before` и mode;
- stale/tampered wrapper, manifest, snapshot, candidate SHA, venv и symlink
  path отклоняются без switch;
- schema-valid подмена каждого manifest path, mode snapshot file/directory,
  candidate HEAD mismatch, dirty current runtime и symlink parent будущего
  candidate/snapshot отклоняются fail-closed;
- все pre-replace failure injections удаляют exact rollout temp; directory
  fsync и post-install verification после replace подтверждают изменённый
  wrapper и структурированный `replacement_applied=true`;
- partial prepare failures оставляют только exact candidate/snapshot evidence,
  не меняют stable wrapper и закрывают повторный prepare;
- test double Git runner подтверждает отсутствие `reset`, `clean`, `rm` и
  иных delete primitives.

Tests запускаются только через
`scripts/run_tests.sh tests/scripts/test_hermes_kanban_mcp_rollout.py`.
Live apply и process/smoke отсутствуют в automated suite.

### 13. `bootstrap-prepare` является единственным входом из export layout

PR #16 не считается ошибочным: обычный `prepare` сохраняет Git-to-Git
precondition. Bootstrap-helper PR SHALL добавить только одну новую команду:

```text
python scripts/hermes_kanban_mcp_rollout.py bootstrap-prepare \
  --source-repo ABS_EXISTING_GIT_WORKTREE \
  --state-root ABS_NONEXISTENT_PATH \
  --export-runtime ABS_EXISTING_PATH \
  --export-manifest ABS_EXISTING_FILE_UNDER_EXPORT \
  [--expected-export-manifest-sha256 FULL_SHA256] \
  --expected-source-commit FULL_GIT_SHA \
  --venv-dirname .venv|venv \
  --expected-venv-interpreter-sha256 FULL_SHA256 \
  --stable-wrapper ABS_EXISTING_FILE \
  --expected-current-wrapper-sha256 FULL_SHA256 \
  [--apply]
```

Отсутствие `--apply` строит полный JSON plan без write primitives. Dry-run
MAY не получать expected manifest SHA-256: тогда он вычисляет и печатает
наблюдаемый byte hash для exact approval. `--apply` MUST требовать
`--expected-export-manifest-sha256` и сравнить его с текущими bytes, чтобы
apply не принял незаметно изменившийся manifest после dry-run. Команда
требует, чтобы state root отсутствовал, а его canonical non-symlink parent
уже существовал и прошёл broad-target checks. `--apply` создаёт ровно этот
leaf через exclusive `mkdir` с итоговым mode `0700`; parent, соседние paths и
export runtime не создаются и не меняются.

Экспортный манифест обязан быть обычным файлом без символьных ссылок строго
внутри экспортированной среды. Его сырые байты сначала проверяются на
отсутствие `NUL` и хэшируются без преобразований, затем файл строго
декодируется как `UTF-8`. Разборщик принимает только непустые строки
`key=value`, разделяя строку по первому `=`. Ключ обязан быть непустым и
уникальным; пустая строка, строка без `=`, пустой ключ, повтор ключа,
ошибка `UTF-8` или `NUL` отклоняют манифест целиком. Пустое значение и
дополнительный `=` внутри значения разрешены.

Ключ `source_commit` обязан присутствовать ровно один раз, содержать полный
`Git SHA` и совпадать с явно переданным
`--expected-source-commit`. Неизвестные ключи разрешены, но их значения не
выводятся в план и не копируются в снимок. Разборщик не вводит общую систему
конфигурации, библиотеку схем или вторую политику манифеста: это один
локальный контракт чтения экспортного `manifest.txt`. Репозиторий источника
обязан содержать этот точный объект коммита. Связь предок→потомок между
источником и целью не проверяется: изолированное рабочее дерево строится по
идентичности объекта, а не по топологии.

Точный `SHA-256` сырых байтов остаётся защитой доверия независимо от разбора
строк. Пробный запуск без ожидаемого хэша печатает наблюдаемый хэш.
`--apply` обязан получить `--expected-export-manifest-sha256` и сравнить его
с повторно прочитанными сырыми байтами до записи.

Baseline path детерминирован:
`<state-root>/hermes-kanban-mcp-<SOURCE_COMMIT>`. Helper создаёт его через
`git worktree add --detach` и требует exact HEAD и пустой tracked status
`--untracked-files=no`. Затем он переносит только exact top-level venv
directory. Export и baseline interpreter должны совпасть с explicit
interpreter SHA-256; executable mode export interpreter фиксируется в
snapshot и повторно проверяется на baseline. Никакие другие export files не
копируются.

Wrapper обязан быть executable regular non-symlink UTF-8 file, совпасть с
explicit SHA-256, содержать standalone `mcp serve-kanban --allow-write`,
ровно один раз содержать exact export runtime path и ещё не содержать
baseline path. `wrapper.after` является единственной byte transformation:
одна замена exact export path на exact baseline path. Stable wrapper в
`bootstrap-prepare` не меняется.

Альтернатива: ослабить обычный `prepare`, чтобы он принимал non-Git current
runtime. Она отвергнута: тогда schema и switch policy перестают отличать
доказанный export от immutable Git runtime.

### 14. Schema v2 объединяет bootstrap и rollout без второй transition policy

Schema v1 из PR #16 недостаточна: она предполагает два разных Git SHA и
Git-clean current runtime. Bootstrap имеет export и baseline с одним
`source_commit`, но разными runtime kinds. Bootstrap-helper PR SHALL
однократно перейти на `schema_version=2`; schema v1 reader/migration не
добавляется.

Каждый v2 manifest содержит общий exact set:

- `schema_version=2`, `snapshot_kind=bootstrap|rollout`, UTC `created_at`;
- `source_repo`, `runtime_root`, `state_root`, `snapshot_id`,
  `stable_wrapper`;
- `before_runtime_kind=export|git`, `before_runtime_path`,
  `before_runtime_sha`, `before_manifest_path`,
  `before_manifest_sha256`;
- `after_runtime_kind=git`, `after_runtime_path`, `after_runtime_sha`;
- `venv_dirname`, `venv_interpreter_sha256`, `venv_interpreter_mode`;
- `wrapper_before_sha256`, `wrapper_after_sha256`, `wrapper_mode`,
  `runtime_path_replacements`.

Для `bootstrap` before manifest fields являются non-null absolute path и
full SHA-256; before/after SHA оба равны source commit; replacement count
равен ровно `1`. Для `rollout` manifest fields равны JSON `null`, before и
after являются exact Git commits и различаются. Variant с иными
kind/null/hash combinations отклоняется.

Snapshot IDs детерминированы:

- bootstrap: `bootstrap-<SOURCE_COMMIT>`;
- rollout: `<CURRENT_COMMIT>-to-<TARGET_COMMIT>`.

Snapshot directory остаётся
`<state-root>/snapshots/<snapshot-id>` с mode `0700` и ровно тремя files
`manifest.json`, `wrapper.before`, `wrapper.after` mode `0600`.

Live schema migration не нужна и backward compatibility не добавляется,
потому что зафиксированный evidence подтверждает отсутствие dedicated state
root, baseline, target и snapshots. Перед любым будущем live
`bootstrap-prepare --apply` оператор обязан отдельно подтвердить, что exact
state root всё ещё отсутствует; существующий root, включая schema v1
artifacts, закрывает apply без cleanup или migration.

### 15. Один validator обслуживает `switch/rollback` обоих snapshot kinds

Существующие `switch/rollback` SHALL читать schema v2 через одну общую
snapshot loader/validator функцию и использовать существующий единый
примитив `_atomic_replace`. Отдельный обработчик `switch/rollback` для `bootstrap`,
отдельная atomic policy или дублированные stale-wrapper guards запрещены.

Общая проверка всегда подтверждает snapshot hashes/modes, exact derived
пути, явно заданные SHA-256 и режим текущего `wrapper`, а также точную `before→after`
replacement и after runtime exact detached Git HEAD/tracked cleanliness/venv
evidence.

Для `snapshot_kind=bootstrap` общий валидатор дополнительно при каждом
пробном запуске и `--apply`:

1. повторно читает экспортный `manifest.txt` по закреплённому пути;
2. сравнивает точный `SHA-256` сырых байтов и заново применяет тот же
   контракт `key=value`, включая `source_commit`;
3. повторно проверяет export venv/interpreter evidence;
4. проверяет baseline exact HEAD, tracked cleanliness и baseline interpreter;
5. требует wrapper before для `switch` и wrapper after для `rollback`.

Для `snapshot_kind=rollout` validator проверяет оба Git runtime и их venv по
существующей policy. Switch и rollback отличаются только required current
wrapper hash и выбором `wrapper.after`/`wrapper.before`; rollback
восстанавливает exact bytes и mode.

После отдельно одобренного bootstrap switch обычный `prepare` получает
baseline как `--current-runtime` и target SHA. Для этого dedicated layout
`runtime-root` и `state-root` MAY быть одним exact canonical root. Это не
ослабляет containment: equality разрешена только как unified layout;
неравные nested roots по-прежнему запрещены, candidate всегда находится в
`hermes-kanban-mcp-<SHA>`, snapshot — только в `snapshots/<ID>`, а roots
никогда не являются write targets целиком.

### 16. File split следует ownership, а не командам

Текущий executable helper имеет 850 строк, а существующий rollout test file —
913. Bootstrap-helper PR MUST удержать каждый source/test file ниже 1000
строк.

Разрешена одна extraction:

- `scripts/hermes_kanban_mcp_rollout.py` владеет argparse, command contexts,
  dry-run plans и `prepare`/`bootstrap-prepare` orchestration;
- `scripts/hermes_kanban_mcp_rollout_state.py` владеет schema v2
  serialization/validation, snapshot files и общей atomic `switch/rollback`
  политику перехода.

Это реальная ownership boundary: persistent rollback evidence и transition
policy отделены от способов построения runtimes. Модуль не является thin
wrapper, не получает второй CLI и не дублирует path/hash primitives.
Bootstrap tests живут в отдельном
`tests/scripts/test_hermes_kanban_mcp_bootstrap.py`; существующий rollout test
file проверяет regression обычного prepare/switch/rollback и schema v2.

Оба файла тестов используют только временные деревья
`Git`/экспорта/среды. Корректный тестовый `manifest.txt` использует фактические
ключи `source_commit`, `deployed_utc`, `python_version`, `mcp_version`,
`command` в формате строк `key=value`. Проверки обязаны включать дубликат
ключа, повреждённую или пустую строку, пустой ключ, `NUL`, ошибку `UTF-8`,
несовпадение `source_commit`, неизвестный ключ, подмену сырых байтов после
пробного запуска и отсутствие значений неизвестных ключей в плане и снимке.

Остальная матрица сохраняет полный файловый снимок до и после пробного
запуска, отсутствие корня состояния до `--apply`, точный режим корня, точный
`HEAD` базовой среды, копирование только `venv`, число замен `wrapper`,
сохранение частичных свидетельств, побайтовую идентичность
`switch`/`rollback` для `bootstrap`, обычный переход
`prepare` от базовой среды к цели при едином корне и лимит строк `<1000`.
Тесты не читают исходный текст и не обращаются к рабочим путям.

### 17. Bootstrap-helper PR не является live approval

Bootstrap-helper PR добавляет capability и temp-only tests, но не создаёт
даже пустой live state root. В PR фазе запрещены live apply, baseline,
снимок, изменение `wrapper`, обычный `prepare` и `process/service/network/DB`
actions и smoke.

После merge требуется новый gate: exact `bootstrap-prepare` dry-run,
сопоставление всех paths/hashes/source commit с одобренным evidence и
отдельное разрешение на `--apply`. Merge PR, зелёные tests или одобрение
этого design сами по себе live apply не разрешают.

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
- [Dry-run сам создаёт candidate/snapshot] → plan-only code path не вызывает
  ни одного write primitive; filesystem before/after oracle покрывает все три
  команды.
- [Stale wrapper переключается поверх чужого изменения] → обязательный
  exact SHA-256 guard на prepare/switch/rollback и повторная проверка сразу
  перед `os.replace`.
- [Path escape или symlink направляет запись наружу] → абсолютные allow-listed
  roots, canonical containment, запрет symlink managed paths и fail-closed
  точные вычисленные пути candidate/snapshot.
- [Rollback snapshot захватывает secrets] → snapshot содержит только
  manifest и bytes стабильного wrapper; runtime/Hermes home/env/DB не
  копируются и не читаются.
- [Копирование runtime переносит dirty patch] → candidate создаётся из exact
  Git object, переносится только выбранный venv, tracked cleanliness обоих
  worktree проверяется.
- [Non-Git export ошибочно принимается обычным prepare] → сохранить Git
  precondition обычного prepare и ввести только explicit `bootstrap-prepare`.
- [Source и target не ancestor] → не выполнять merge/rebase/ancestry gate;
  проверять каждый exact commit object и exact detached HEAD независимо.
- [Bootstrap получает отдельную switch policy] → schema v2 variant и один
  общий loader/validator/atomic transition для обоих snapshot kinds.
- [State root частично создан после ошибки] → не очищать автоматически;
  сохранять exact evidence и закрывать повторный apply на existing root.
- [Schema v1 migration расширяет риск] → не поддерживать v1, так как live
  snapshots отсутствуют; existing state root всегда требует stop/replan.
- [Helper/test превышает 1000 строк] → одна ownership extraction для
  snapshot/transition policy и отдельный bootstrap test file.

## План доставки

1. Сохранить закрытыми выполненные PR #15 на
   `062f2f0f1f6947830d1b222a3ef470e145a7c34d` и PR #16 на
   `9fcd66651768e3cf220d5cd501efbec5ae3e2550`.
2. Реализовать отдельным task-owned bootstrap-helper PR только новую command,
   schema v2/shared transition ownership module и temp-only tests.
3. До merge не создавать live state root/baseline/snapshot, не менять wrapper
   и не выполнять обычный prepare, process/service/network/DB actions или
   smoke.
4. Запустить оба focused helper test files через `scripts/run_tests.sh`,
   проверить line counts `<1000`, strict OpenSpec validation и exact diff
   scope.
5. Получить независимое code review без `BLOCK`, затем создать отдельный PR;
   merge PR не открывает live gate.
6. После merge отдельно выполнить только `bootstrap-prepare` dry-run,
   сопоставить exact evidence и запросить live approval.
7. Только после approval выполнить `bootstrap-prepare --apply`, проверить
   schema v2 snapshot/baseline и снова отдельно пройти dry-run `switch`.
8. После одобренного bootstrap switch обычный `prepare` строит target из
   baseline; каждый последующий apply остаётся отдельным gated шагом.
9. Process replacement и bounded MCP smoke выполняются только после
   repository lifecycle и не входят в bootstrap-helper PR.
10. При провале использовать тот же schema v2 `rollback`; не удалять
    baseline/target/snapshots и не менять глобальный Hermes symlink,
    Hermes/Gurra, services, connector config или live DB.

Будущий rollback, если отдельный rollout будет одобрен, ограничивается
`rollback --apply` по exact snapshot, возвратом предыдущего standalone MCP
process и повторным bounded smoke. Candidate/snapshot сохраняются как
evidence; автоматического cleanup нет.

## Открытые вопросы

Блокирующих архитектурных вопросов нет. Единственное material divergence от
предпочтительного shape — schema v1 заменяется schema v2 и допускается единый
runtime/state root: без этого bootstrap snapshot нельзя безопасно consume
существующими `switch/rollback`, а обычный `prepare` не сможет построить
target рядом с baseline. Scope live действий не расширяется.
