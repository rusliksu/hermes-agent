> **Текущий статус REMEDIATION BASELINE ПОСЛЕ BLOCK:** independent review
> дельты 26.x вернул `BLOCK`. Claims 26.3–26.5 сохранены только как
> historical implementation evidence, superseded и не приняты как текущая
> acceptance. Baseline 27.x явно одобрен, valid red и minimal repo-local
> implementation 27.3–27.4 выполнены; один exact five-module author run дал
> `180 passed`. Два последовательных run 27.5, independent acceptance,
> commit/push/PR и live-действия не разрешены.

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

После двух remediation cycles третье независимое ревью
`20260729T161437Z-kanban-runtime-coherence-final-review` снова вынесло
`BLOCK`. Python-level audit/blocklist не перекрывает low-level/native paths,
candidate interpreter стартует до появления policy, schema-v2 compatibility
не закреплена реальным историческим golden, а rollout test достиг 996 строк.
На HOSTKEY зафиксированы bubblewrap `0.9.0` и работоспособный Codex sandbox.
Material OS-sandbox delta ниже явно утверждён пользователем 2026-07-29 только
для implementation и repo-local/temp-only verification; delivery и live
actions этим approval не разрешены.

Независимый run
`20260729T192126Z-kanban-os-sandbox-independent-review` после этой реализации
снова вынес `BLOCK`. Review показал, что parent anchors и `bwrap` открывают
не одни и те же runtime/venv/interpreter/stdlib objects; swap-and-restore
может запустить другой interpreter и затем скрыть подмену. Оно также
зафиксировало raw `/home/openclaw` в `provenance.json`, forwarding façade и
всего 26 строк запаса в `runtime_coherence.py`, потерю четырёх security
regressions и overclaim полного capability probe. Оба независимых four-suite
run остановились до collection, потому что review sandbox был read-only и не
предоставил usable temp/cache path. Эти факты образуют новый material
baseline и не закрываются предыдущим approval.

Независимый run
`20260729T224514Z-kanban-remediation-independent-review` выполнил exact
four-suite команду два раза подряд успешно, но вынес новый `BLOCK`.
Зелёные runs подтверждают только regression evidence. Directory descriptor
не замораживает bytes вложенного regular file: после построения anchors файл
можно изменить in-place, выполнить candidate import/effect и восстановить,
при этом directory identity и forged child evidence останутся
согласованными. Review также обнаружил неполный FD cleanup при
`write`/`lseek`/seal failure и отсутствие содержательной разгрузки:
rollout test имеет 999 строк, а support — 40.

Reviewer-only probe временно менял source file, затем побайтово восстановил
его; pre/post fingerprints совпали. Это честно зафиксированный deviation, а
не implementation change. Поскольку mandatory independent review должен
быть source-read-only, probe исключён из acceptance evidence и в следующем
цикле не повторяется как обязательное действие.

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
- удержать rollout test `<=850` строк, существующий reusable support `<400`,
  каждый source/test file ниже 1000 строк; общие path/Git/venv primitives
  должны иметь отдельного настоящего owner без forwarding façade, а
  `runtime_coherence.py` — минимум 100 строк запаса до лимита.
- установить OS-level containment exact `/usr/bin/bwrap` до запуска любого
  candidate Python и завершаться fail-closed без Python-only fallback;
- материализовать каждый исполняемый/импортируемый regular file candidate
  source, exact interpreter, required trusted stdlib/runtime closure и
  `bwrap` в sealed immutable content bundle; строить anchors/digests из тех
  же bytes и не bind mutable backing directory;
- строить manifest descriptor-relative с `O_NOFOLLOW`, fail-closed при
  неполном/изменившемся capture и гарантировать exact captured verified
  bytes только от anchor construction до `exec`/import;
- сделать ownership каждого `open`/`memfd` exception-safe и проверяемым
  failure injection без leaked FDs или скрытых cleanup errors;
- определить security contract как отсутствие host-visible side effects,
  сохранив Python audit/sticky denial вторым слоем и evidence;
- закрепить schema-v2 rollback полностью sanitized historical golden:
  raw `/home/openclaw` отсутствует во всех четырёх fixture files, а
  provenance ledger хранит только классы/хэши исходных значений;
- разгрузить rollout test до `<=850` строк, превратив существующий
  `hermes_kanban_mcp_test_support.py` в содержательного reusable owner
  общего Git/layout/oracle harness размером `<400` строк; behavior unchanged.

**Вне целей:**

- merge или cherry-pick старой adapter ветки целиком;
- новая sync-логика, title/fuzzy/source-path lookup или batch sync MCP tool;
- смешивание OpenSpec source-definition persistence с существующим terminal
  `sync_external_task`;
- новый обычный Hermes model/core tool;
- новая зависимость или DB migration;
- новые daemon/root/deploy requirements, `nsjail` или `systemd-run`;
- обязательный seccomp dependency; seccomp допускается только как отдельное
  future hardening после доказанного acceptance tests остаточного риска;
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

### 10. Helper сохраняет один CLI и явные rollout phases

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
  [--expected-wrapper-after-sha256 FULL_SHA256] \
  [--apply]

python scripts/hermes_kanban_mcp_rollout.py switch \
  --runtime-root ABS_PATH \
  --state-root ABS_PATH \
  --snapshot-id SNAPSHOT_ID \
  --stable-wrapper ABS_PATH \
  --expected-current-wrapper-sha256 FULL_SHA256 \
  [--expected-wrapper-after-sha256 FULL_SHA256] \
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
4. разбирает поддерживаемый current wrapper и строит candidate
   `wrapper.after` единственным canonical generator как
   `source-cwd-nofile-v2` для exact candidate path, сохраняя
   `wrapper.before` byte-identical и executable mode;
5. создаёт immutable rollback snapshot и завершает работу без переключения
   стабильного wrapper.

Current wrapper разрешён только если он является обычным несимвольным
executable UTF-8 файлом и проходит exact allow-listed legacy/canonical
parser с запускаемым контрактом `mcp serve-kanban --allow-write`. Candidate
path не должен уже быть active runtime. Иначе helper завершается fail-closed.
Broad rewrite или отдельный bootstrap/rollout template запрещены.

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

### 11. Исторический PR #16: schema v1 фиксировал Git-to-Git rollout

Этот раздел сохраняет delivery evidence PR #16 и superseded текущим
creation contract разделов 14/19/29. В PR #16 snapshot ID детерминированно
имел вид
`<CURRENT_FULL_SHA>-to-<CANDIDATE_FULL_SHA>` и создавался эксклюзивно под
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
  [--expected-wrapper-after-sha256 FULL_SHA256] \
  [--apply]
```

Отсутствие `--apply` строит полный JSON plan без примитивов записи. Dry-run
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

Dry-run вычисляет и сообщает exact canonical `wrapper.after` SHA-256, но не
требует `--expected-wrapper-after-sha256`. `bootstrap-prepare --apply`,
`prepare --apply` и `switch --apply` MUST получить этот аргумент и сравнить
его с actual generated/snapshot bytes до первого managed write, candidate
preflight и `os.replace`. Missing или mismatch завершается fail-closed.
`rollback` не получает этот аргумент и сохраняет прежний snapshot-only CLI.

Baseline path детерминирован:
`<state-root>/hermes-kanban-mcp-<SOURCE_COMMIT>`. Helper создаёт его через
`git worktree add --detach` и требует exact HEAD и пустой tracked status
`--untracked-files=no`. Затем он переносит только exact top-level venv
directory. Export и baseline interpreter должны совпасть с explicit
interpreter SHA-256; executable mode export interpreter фиксируется в
snapshot и повторно проверяется на baseline. Никакие другие export files не
копируются.

Wrapper обязан быть executable regular non-symlink UTF-8 file, совпасть с
explicit SHA-256, содержать exact allow-listed standalone
`mcp serve-kanban --allow-write` grammar, ссылаться на exact export runtime
как active before path и ещё не использовать baseline path.
`wrapper.before` сохраняется byte-identical, а fresh `wrapper.after` строится
тем же canonical `source-cwd-nofile-v2` generator для baseline, что и
ordinary rollout. Stable wrapper в `bootstrap-prepare` не меняется.

Альтернатива: ослабить обычный `prepare`, чтобы он принимал non-Git current
runtime. Она отвергнута: тогда schema и switch policy перестают отличать
доказанный export от immutable Git runtime.

### 14. Fresh bootstrap и rollout имеют один schema-v3 contract

Исторические PR #16/bootstrap-helper последовательно выпускали schema v1 и
schema v2. Это implementation history, а не действующий creation contract.
Fresh `bootstrap-prepare --apply` и ordinary `prepare --apply` SHALL
создавать только `schema_version=3` с exact
`snapshot_kind=bootstrap|rollout` и
`wrapper_contract=source-cwd-nofile-v2`.

Схема v3 сохраняет существующую модель сред выполнения «до/после» без
расширения формы манифеста:

- `schema_version=3`, `snapshot_kind=bootstrap|rollout`, UTC `created_at`;
- `source_repo`, `runtime_root`, `state_root`, `snapshot_id`,
  `stable_wrapper`;
- `before_runtime_kind=export|git`, `before_runtime_path`,
  `before_runtime_sha`, `before_manifest_path`,
  `before_manifest_sha256`;
- `after_runtime_kind=git`, `after_runtime_path`, `after_runtime_sha`;
- `venv_dirname`, `venv_interpreter_sha256`, `venv_interpreter_mode`;
- `wrapper_contract=source-cwd-nofile-v2`;
- `wrapper_before_sha256`, `wrapper_after_sha256`, `wrapper_mode`,
  `runtime_path_replacements`;
- уже определённое runtime-coherence evidence.

Для fresh `bootstrap` before manifest fields являются non-null absolute path
и full SHA-256; before/after SHA оба равны source commit; replacement count
равен ровно `1`. Fresh bootstrap `wrapper.after` MUST строиться тем же
единственным canonical generator, что ordinary rollout, включая exact
`ulimit -S -n 4096`, `cd --` и `exec`.

Любой `schema_version!=3`, а также historical schema-v3 snapshot с
`wrapper_contract=source-cwd-v1`, MUST быть отклонён switch loader до
candidate preflight, первого managed write и `os.replace`. Эти artifacts
MAY читаться только отдельным snapshot-only rollback loader для exact
восстановления bytes/mode. In-place migration, rewrite или «upgrade»
существующего snapshot запрещены.

Snapshot IDs детерминированы:

- bootstrap: `bootstrap-<SOURCE_COMMIT>`;
- rollout: `<CURRENT_COMMIT>-to-<TARGET_COMMIT>`.

Snapshot directory остаётся
`<state-root>/snapshots/<snapshot-id>` с mode `0700` и ровно тремя files
`manifest.json`, `wrapper.before`, `wrapper.after` mode `0600`.

Live schema migration не нужна. Перед любым будущим live
`bootstrap-prepare --apply` оператор обязан отдельно подтвердить, что exact
state root всё ещё отсутствует; существующий root закрывает apply без cleanup
или migration.

### 15. Проверка switch/runtime отделена от rollback только по snapshot

`switch` SHALL использовать полную проверку согласованности runtime только
для новых снимков schema v3: точный `snapshot_kind`, контракт
`source-cwd-nofile-v2`, разобранный мягкий лимит `4096`, хеш манифеста,
фактические байты/хеш `wrapper.after`, точные производные пути, guard текущего
wrapper, target runtime/venv и import-origin evidence. Bootstrap switch дополнительно
повторно проверяет export manifest, source commit, export venv и exact
baseline runtime. Один atomic replacement primitive и общие path/hash
primitives сохраняются; отдельная atomic policy запрещена.

`rollback` SHALL использовать отдельный snapshot-only loader/validator. Он
читает только exact `manifest.json`, `wrapper.before`, `wrapper.after`,
проверяет исторически поддерживаемую schema/grammar, размеры/modes/hashes
snapshot, exact snapshot ID и stable-wrapper path, явный current-wrapper
SHA-256 guard и соответствие current wrapper exact `wrapper.after`. Затем
тот же atomic primitive восстанавливает exact bytes/mode `wrapper.before`.
Rollback loader не мигрирует manifest или wrapper и не делает artifact
switch-eligible.

Rollback только по snapshot MUST NOT:

1. требовать существования или cleanliness source repo;
2. требовать существования, Git HEAD, venv или interpreter candidate/baseline;
3. импортировать target modules или запускать import-origin preflight;
4. читать export manifest/runtime, даже для bootstrap snapshot;
5. ослаблять exact snapshot/hash/current-wrapper/`wrapper.before` guards.

Поэтому missing, corrupt или dirty candidate не блокирует emergency rollback.
Ошибка только rollback-owned evidence по-прежнему закрывает запись
fail-closed.

После отдельно одобренного bootstrap switch обычный `prepare` получает
baseline как `--current-runtime` и target SHA. Для этого dedicated layout
`runtime-root` и `state-root` MAY быть одним exact canonical root. Это не
ослабляет containment: equality разрешена только как unified layout;
неравные nested roots по-прежнему запрещены, candidate всегда находится в
`hermes-kanban-mcp-<SHA>`, snapshot — только в `snapshots/<ID>`, а roots
никогда не являются write targets целиком.

### 16. File split сохраняет реальные ownership boundaries и отдельный common owner

Исторический bootstrap-helper baseline удерживал files ниже 1000 строк.
Текущая truth state после remediation: rollout test — 999 строк, support —
40. Новый material gate требует не только hard limit, но и измеримый запас:
rollout test `<=850`, support `<400`, каждый source/test `<1000`.

Material remediation требует отдельную extraction:

- `scripts/hermes_kanban_mcp_rollout.py` владеет argparse, command contexts,
  dry-run plans и `prepare`/`bootstrap-prepare` orchestration;
- `scripts/hermes_kanban_mcp_rollout_state.py` владеет schema v2
  и v3 serialization, snapshot files, snapshot-only rollback validation и
  общей atomic transition policy;
- `scripts/hermes_kanban_mcp_runtime_coherence.py` владеет exact
  точной грамматикой и генерацией legacy/canonical wrapper, а также
  политикой изолированной проверки происхождения импортов.
- отдельный common ownership module владеет общими path/Git/venv primitives,
  которые нужны более чем одному из orchestration/state/coherence modules.
  Точное имя файла и внутренняя группировка helpers не являются контрактом.

Это реальные ownership boundaries: persistent rollback evidence и transition
policy отделены от построения runtimes, а shell/import security boundary —
от schema/snapshot state. Общие primitives определены ровно в одном common
owner; consumers импортируют их непосредственно. State module не
реэкспортирует их и не сохраняет forwarding façade. Ни один модуль не
получает второй CLI и не дублирует path/hash primitives.
`runtime_coherence.py` MUST иметь измеримый запас не менее 100 строк до
hard limit 1000, то есть не более 900 физических строк; остальные
source/test files остаются `<1000`.
Общий Git/layout/oracle test harness MUST содержательно принадлежать
существующему `tests/scripts/hermes_kanban_mcp_test_support.py`; support не
может быть thin forwarding façade. Extraction MUST сохранять behavior.
Bootstrap tests живут в отдельном
`tests/scripts/test_hermes_kanban_mcp_bootstrap.py`; существующий rollout test
file проверяет regression обычного prepare/switch/rollback и schema v2/v3, а
`tests/scripts/test_hermes_kanban_mcp_runtime_coherence.py` проверяет exact
wrapper grammar, parent evidence и исторический schema-v2 golden.
`tests/scripts/test_hermes_kanban_mcp_runtime_sandbox.py` отдельно проверяет
OS containment, bypass attempts и host canaries.
`tests/scripts/test_hermes_kanban_mcp_rollout_state.py` отдельно владеет
регрессиями схемы, типа, контракта, хеша и загрузчика и входит в обязательный
целевой набор.

Все пять helper test modules используют только временные деревья
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
RLIMIT scenarios запускаются через отдельный child Python trampoline,
который внутри дочернего процесса устанавливает контролируемые soft/hard
limits и запускает wrapper. Они не зависят от ambient hard/infinity и не
используют `preexec_fn`.

### 17. Bootstrap-helper PR не является разрешением на live-действия

Bootstrap-helper PR добавляет capability и temp-only tests, но не создаёт
даже пустой live state root. В PR фазе запрещены live apply, baseline,
снимок, изменение `wrapper`, обычный `prepare` и `process/service/network/DB`
actions и smoke.

После merge требуется новый gate: exact `bootstrap-prepare` dry-run,
сопоставление всех paths/hashes/source commit с одобренным evidence и
отдельное разрешение на `--apply`. Merge PR, зелёные tests или одобрение
этого design сами по себе live apply не разрешают.

### 18. Material repair baseline: откат завершён, причина rollout failure локализована

Зафиксированный rollback evidence является новой baseline-точкой для
ремонта: rollback wrapper для snapshot
`6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`
успешно применён с `exit 0`. Stable wrapper восстановлен до SHA-256
`17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`.
Restart, process replacement, DB, Kanban operations и smoke не выполнялись.
Это закрывает emergency rollback step, но не даёт нового rollout evidence.

Root cause: candidate runtime содержал target source checkout с новым
`kanban_sync_external_task`, но candidate `venv` был скопирован из старой
среды. Запуск `candidate/venv/bin/python -m hermes_cli.main` без явного
source cwd импортировал `hermes_cli.main` и
`agent.transports.hermes_kanban_mcp_server` из old `site-packages`, а не из
target checkout. Поэтому runtime `WRITE_TOOLS` оставался старым, несмотря на
правильный target path в wrapper.

Исправление не должно решать это через network, `pip install`, editable install
или файл `.pth`. Candidate venv остаётся переносимым артефактом, а source
checkout становится каноническим корнем imports через wrapper cwd и preflight
guards. Это меньше меняет состояние runtime и не требует изменения package.

### 19. Schema v3 и canonical `source-cwd-nofile-v2` wrapper

Все fresh bootstrap и ordinary rollout snapshots SHALL использовать
`schema_version=3`, `snapshot_kind=bootstrap|rollout` и
`wrapper_contract=source-cwd-nofile-v2`. Schema v3 сохраняет общую
before/after runtime модель, но добавляет evidence согласованности для imports
из target source:

- `wrapper_contract=source-cwd-nofile-v2`;
- deterministic `wrapper.after` bytes и SHA-256;
- exact target runtime cwd, exact target interpreter path и exact module
  origin paths для `hermes_cli.main` и
  `agent.transports.hermes_kanban_mcp_server`;
- exact `WRITE_TOOLS` evidence, включающее
  `kanban_sync_external_task`;
- preflight command/result metadata без environment dump, secrets, DB data
  или wrapper text.

Canonical `source-cwd-nofile-v2` wrapper SHALL установить process-local
finite soft limit exact строкой `ulimit -S -n 4096`, затем явно выполнить
`cd -- <EXACT_TARGET_RUNTIME>` непосредственно перед запуском
`<EXACT_TARGET_RUNTIME>/<venv>/bin/python -m hermes_cli.main ...`. `cd`
является частью контракта runtime, а не косметикой: он делает target checkout
первым источником imports для `python -m`, чтобы copied venv `site-packages` не
затенял source tree.

Legacy schema v2 wrapper и historical schema-v3 `source-cwd-v1` допускаются
как `before` и rollback target. Helper MUST сохранять exact
`wrapper.before` bytes/mode и MAY rollback такие snapshots без проверок
import-origin candidate. Switch MUST отклонять любой `schema_version!=3` и
historical schema-v3 `source-cwd-v1` до preflight/mutation. In-place
migration отсутствует.

Wrapper parser/generator SHALL быть deterministic и fail-closed и принадлежать
`scripts/hermes_kanban_mcp_runtime_coherence.py`. Parser принимает только
явно перечисленные exact legacy/canonical templates: корректный shebang,
ожидаемый `set`, exact allow-listed exports и единственный исполняемый
`exec` с exact argv `-m hermes_cli.main mcp serve-kanban --allow-write "$@"`.
Canonical template дополнительно требует exact
`ulimit -S -n 4096`, затем `cd -- <runtime>` непосредственно перед `exec`.
Fresh bootstrap `wrapper.after` строится этим же generator, а не отдельной
path-rewrite веткой.

Parser MUST отклонять comments-only совпадение, отсутствующий `exec`,
дополнительную команду до или после него, redirects, pipes, backgrounding,
command substitution и shell control operators, лишние argv/exports,
несколько runtime/interpreter paths, смешанные baseline/candidate paths,
symlink wrapper и non-executable wrapper. Подстроки или token-presence не
являются доказательством поддерживаемой grammar.

### 20. Bubblewrap устанавливает boundary до candidate Python

Новый `prepare` сначала строит target worktree/venv по существующей
fail-closed policy и deterministic `wrapper.after`, затем доверенный parent
готовит anchors и запускает sanitized import-origin preflight. Candidate
Python никогда не стартует напрямую: единственная допустимая цепочка —
зафиксированный и запечатанный образ `bwrap` → зафиксированный и запечатанный
интерпретатор кандидата
`-I -S -B`. Exact `/usr/bin/bwrap` является allow-listed capture source, а
не повторно открываемым executable после anchor construction.
На HOSTKEY наблюдаются bubblewrap `0.9.0` и работоспособный Codex sandbox, но
это только входной planning evidence. Каждый production preflight обязан
проверить exact executable и фактическую работоспособность containment.
Отдельный capability probe является только baseline probe: он проверяет
базовую возможность запустить exact `bwrap` с обязательными namespaces и
synthetic mounts, но не утверждает, что проверил candidate-specific
sealed content/data binds. Полный профиль доказывает только реальный
production invocation со всем sealed bundle и exact candidate argv. Любая
ошибка построения или выполнения этого invocation закрывает
операцию fail-closed; Python-only, unsandboxed или частичный fallback
отсутствует.

Минимальный профиль `bwrap`:

- новый пустой mount namespace без bind host `/`;
- candidate/source/venv regular files доступны только из sealed content
  bundle; directory/symlink topology создаётся из полного manifest;
- exact interpreter, необходимые stdlib, loader/shared-library regular files
  и bytes `bwrap` входят в тот же sealed capture closure. Mutable candidate,
  `/usr`, `/lib*` или другой backing directory целиком не bind-mount-ится;
  произвольные `/etc`, host home, runtime state и host sockets не монтируются;
- отдельные tmpfs mounts для temp, `HOME` и `HERMES_HOME`, чтобы эти три
  области не разделяли host backing store;
- `--clearenv`, затем точный allowlist:
  `HOME=/sandbox/home`, `HERMES_HOME=/sandbox/hermes-home`,
  `TMPDIR=/sandbox/tmp`, `LANG=C.UTF-8`, `LC_ALL=C.UTF-8`, `TZ=UTC`,
  `PYTHONDONTWRITEBYTECODE=1`; `PATH`, `PYTHONPATH`, `PYTHONHOME`,
  credentials и произвольный parent environment отсутствуют;
- свежий `/proc` и минимальный `/dev`, без host device/socket passthrough;
- отдельные user, PID, IPC, UTS, cgroup и network namespaces настолько,
  насколько их поддерживают `bwrap` и kernel. Требуемые flags задаются
  fail-closed: если согласованный профиль нельзя создать целиком, candidate
  не запускается;
- `--new-session` и `--die-with-parent`.

Новые daemon, root privilege, deploy, `nsjail`, `systemd-run` или обязательный
seccomp dependency не добавляются. Seccomp остаётся только возможным future
hardening, если acceptance tests докажут реальный остаточный host-visible
effect, не закрываемый bubblewrap namespaces.

Security contract намеренно узкий и проверяемый: preflight не имеет
host-visible side effects. Он не обещает, что каждый внутренний syscall
вернёт `EPERM`: candidate может создать процесс внутри своего PID namespace
или открыть socket внутри пустого network namespace, но не может изменить
host files, sockets, процессы, signals, resource limits или DB. Существующие
Python audit hook, sticky denial и monkeypatch guards сохраняются как второй
слой, ранняя диагностика и структурированное evidence. Они не являются
boundary и их blocklist не используется как доказательство полноты native
изоляции.

### 21. Sealed content bundle является trust boundary для bytes

Directory descriptor не является content snapshot: вложенный regular file
может измениться in-place без замены directory или inode. Поэтому прежний
descriptor-pinned contract отозван как достаточный security boundary.
Доверенный parent SHALL построить sealed content bundle до любого candidate
`exec`/import.

Capture состоит из двух фаз.

1. Parent descriptor-relative обходит exact allow-listed roots. Каждый
   компонент открывается относительно уже проверенного directory FD с
   `O_NOFOLLOW`; `lstat`/`fstat` различают directory, symlink и regular file.
   Unsupported file type, escape, цикл/ambiguous symlink, неполный manifest
   или изменение проверяемого объекта во время capture закрывают операцию
   fail-closed.
2. Каждый исполняемый или импортируемый обычный файл материализуется в
   отдельный объект данных `memfd`: дерево исходного кода кандидата, точный
   интерпретатор, `pyvenv.cfg`, необходимая доверенная стандартная библиотека,
   замыкание загрузчика и разделяемых библиотек, доверенный тестовый каркас и
   точные байты исполняемого файла `bwrap`. После полной записи
   parent выполняет `lseek`, повторное чтение/hash из memfd и seals,
   запрещающие write/grow/shrink/future mutation. Manifest entry, anchor и
   digest строятся только из уже sealed captured bytes, а не из повторного
   чтения backing path.

Контракт начинается в момент успешного завершения capture. Он не обещает
недостижимую защиту исторических bytes до capture; он обещает, что от anchor
construction до `exec`/import используются exact captured verified bytes.
Если platform не позволяет sealed execution exact interpreter/`bwrap` и
необходимого loader closure, operation завершается fail-closed без path-based
fallback.

Bubblewrap получает только:

- sealed regular-file bundle через read-only data/FD bindings с exact
  manifest destinations и allow-listed modes;
- directory entries и symlinks, созданные из manifest в свежей sandbox
  topology;
- отдельные tmpfs `HOME`, `HERMES_HOME` и temp, минимальные `/proc`/`dev` и
  согласованные namespaces.

Mutable candidate/source/venv, `/usr`, `/lib*` или другой backing directory
не bind-монтируется. Symlink topology не может добавлять path за пределами
manifest. Exact `bwrap` image и его необходимый loader closure запускаются из
captured sealed bytes через FD/fexecve-equivalent путь; обычный
`/usr/bin/bwrap` path используется только для начального capture/diagnostic
identity и не переоткрывается как executable после anchor construction.

FD ownership централизован в одном bundle/resource owner:

1. каждый успешный `open` и `memfd_create` регистрируется немедленно, до
   следующей fallible операции;
2. `_data_fd` владеет current FD до явного handoff и при ошибке `write`,
   `lseek`, повторного чтения/hash или seal закрывает и снимает с регистрации
   именно current FD;
3. при ошибке построения partial bundle owner закрывает все ранее
   приобретённые FDs в обратном порядке;
4. при ошибке handoff/invocation/post-check/switch owner сохраняет тот же
   контракт очистки;
5. cleanup error не подавляется и не подменяет primary cause: наружу выходит
   structured fail-closed error с primary failure, полным списком cleanup
   failures и `replacement_applied` state. Snapshot/switch не продолжаются.

Parent/child evidence сравнивается с manifest/digests sealed bundle. Child
self-report не расширяет allowlist и не заменяет anchors. Nested
in-place mutate→candidate import/effect→restore после capture, даже с
полностью matching forged child JSON, может привести только к исполнению
sealed original bytes либо к fail-closed до import/effect. То же правило
применяется к trusted stdlib regular file и exact interpreter/`bwrap` bytes,
где platform даёт воспроизводимый behavioral oracle. Во всех случаях host
canary не меняется.

Внутри sandbox `-S` отключает автоматический `site`/`.pth`, `-B` исключает
bytecode writes. Origin `hermes_cli.main` определяется через
`importlib.util.find_spec` без top-level import, затем импортируется
`agent.transports.hermes_kanban_mcp_server`; оба origins должны принадлежать
manifest topology sealed target source, а `WRITE_TOOLS` — содержать
`kanban_sync_external_task`. Произвольные `stderr`, parent environment,
реальный `HOME` и секретные значения наружу не отражаются.

`prepare` dry-run не создаёт candidate/bundle и не запускает `bwrap`, поэтому
показывает только deterministic plan/hash/wrapper evidence. Первое sealed
bundle/import-origin evidence появляется на `prepare --apply` до snapshot.
`switch` заново выполняет capture, sandbox preflight и проверки
wrapper/hash/runtime на dry-run и перед atomic replacement.

`rollback` использует только snapshot-owned evidence. Он не создаёт sealed
bundle, не запускает `bwrap`/candidate interpreter и не зависит от source,
venv, interpreter, stdlib или imports. Повреждение либо отсутствие этих
candidate областей не блокирует snapshot-only rollback.

### 22. Schema-v2 compatibility закреплена статическим golden

Compatibility test использует реальный исторический schema-v2 snapshot:
sanitized статические `manifest.json`, `wrapper.before` и `wrapper.after`.
Fixture обязан хранить provenance исторического snapshot/wrapper, исходные
SHA-256 каждого blob и исчерпывающий ordered список substitutions,
выполненных только для удаления host-specific paths/идентификаторов. Для
sanitized bytes также закрепляются собственные SHA-256.
Ни один из четырёх файлов `manifest.json`, `wrapper.before`,
`wrapper.after`, `provenance.json` не содержит raw `/home/openclaw` либо
другое raw source path из этого prefix.

Provenance фиксирует snapshot
`6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`
и исходные SHA-256 до sanitization:

- `manifest.json` —
  `83db7f0c4cd2a3239e5d52402f6b8b88e1a66ca46ba1daa5677249fcac4a196f`;
- `wrapper.before` —
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`;
- `wrapper.after` —
  `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`.

Каждая sanitization substitution MUST перечислять `file/field`, source class,
SHA-256 исходного literal value, literal replacement, число замен и причину.
Raw source value/path в provenance запрещён. Payload bytes и sanitized
SHA-256 `manifest.json`, `wrapper.before`, `wrapper.after` не меняются при
очистке provenance; меняется только metadata fixture `provenance.json`.
Неперечисленная нормализация, перестановка JSON keys/whitespace или
production-generated expected bytes запрещены.

Expected bytes являются literal fixture data. Их запрещено строить schema-v3
prepare flow, production schema constants, wrapper generator или production
rewrite helper. Test отдельно доказывает, что текущий snapshot-only loader
принимает исторический grammar и восстанавливает exact sanitized
`wrapper.before`.

### 23. Приёмка OS-sandbox delta и гейты доставки

Automated acceptance MUST оставаться только во временном окружении и точечной.
Минимальная матрица:

- fresh bootstrap и rollout создают только schema v3 с exact
  `snapshot_kind=bootstrap|rollout`, canonical
  `source-cwd-nofile-v2` в `wrapper.after`; bootstrap использует тот же
  generator;
- rollout из legacy в canonical: старый wrapper в `wrapper.before`, schema v3
  snapshot, canonical `source-cwd-nofile-v2` в `wrapper.after`;
- rollout из canonical в canonical: следующий target сохраняет canonical
  контракт wrapper;
- совместимость schema v2 rollback: существующий v2 snapshot может восстановить
  exact previous wrapper bytes/mode без проверок imports candidate;
- schema v3 switch/rollback: switch повторяет проверки import-origin, rollback
  не зависит от imports candidate;
- schema v2/v3 rollback при missing/corrupt/dirty candidate восстанавливает
  exact `wrapper.before` либо fail-closed только по snapshot/current-wrapper
  evidence;
- любой `schema_version!=3` и historical schema-v3 `source-cwd-v1`
  fail-closed для switch до preflight/mutation, но остаётся отдельным
  snapshot-only exact bytes/mode rollback input без in-place migration;
- `bootstrap-prepare --apply`, `prepare --apply` и `switch --apply` требуют
  `--expected-wrapper-after-sha256`; missing/mismatch завершается до первого
  managed write/preflight/`os.replace`, dry-run только сообщает hash, а
  rollback CLI не меняется;
- exact switch loader повторно проверяет kind, contract, parsed
  `ulimit=4096`, manifest hash и actual `wrapper.after` bytes; отдельный plan
  digest отсутствует, planned soft limit выводится из parsed wrapper и
  форма манифеста не расширяется;
- snapshot-only rollback не запускает `bwrap`, candidate Python и не зависит
  от source/venv/interpreter/import state;
- comments-only wrapper, missing `exec`, extra commands, redirects и control
  operators fail-closed до примитивов записи;
- затенение старым `site-packages`: copied venv содержит старый installed
  package, но wrapper/preflight доказывают imports из target checkout;
- baseline capability probe не называется полным profile probe; полный
  production invocation со всеми sealed content/data binds является
  authoritative проверкой и при missing/broken `/usr/bin/bwrap` либо
  неполном namespace/bind profile закрывается fail-closed без fallback;
- direct `subprocess._fork_exec`, `ctypes`/native write/network, signal и
  `resource.prlimit` attempts не меняют host canary files, sockets, processes
  или limits;
- symlink/TOCTOU interpreter, `pyvenv.cfg`, source и venv swaps fail-closed;
- nested in-place mutate→candidate import/effect→restore после sealed capture
  с полностью совпадающим forged child evidence выполняет только captured
  original bytes либо fail-closed, а host side-effect отсутствует;
- тот же oracle применяется к trusted stdlib regular file и, где практично,
  точным байтам интерпретатора и `bwrap`;
- failure injection на каждой acquisition/capture/handoff стадии доказывает
  немедленную регистрацию FD, закрытие current `_data_fd`, cleanup всего
  partial bundle, отсутствие leaked FDs и structured cleanup error;
- trusted stdlib roots приходят из parent/system interpreter, child self-report
  не расширяет trust;
- preflight использует реальные target modules либо faithful fixture,
  раздельные tmpfs HOME/HERMES_HOME/temp и outside-root/host-canary oracle;
- real HOME не читается и не изменяется, `.pth` не исполняется, произвольный
  `stderr` не отражается;
- historical schema-v2 rollback проверяется статическим sanitized golden с
  provenance, исходными SHA-256 и полным списком substitutions; raw
  `/home/openclaw` отсутствует во всех четырёх fixture files, ledger содержит
  поля `file/field`, `source class/hash`, `literal replacement`, `count/reason`; ожидаемые
  payload bytes не генерирует production helper;
- focused suite сохраняет regression cases existing candidate, existing
  snapshot, symlink stable wrapper и future candidate/snapshot parent
  symlink;
- dry-run без записи oracle для `prepare`, `switch`, `rollback`; prepare
  dry-run не содержит origin evidence, prepare apply и switch dry-run/apply
  содержат;
- точный список tools доказывает присутствие `kanban_sync_external_task` в
  поверхности режима записи;
- общий Git/layout/oracle harness содержательно принадлежит существующему
  `hermes_kanban_mcp_test_support.py`, без thin forwarding; rollout test
  `<=850` строк, support `<400`, behavior unchanged;
- пять helper test modules входят в focused suite; каждый source/test file
  остаётся `<1000` строк, `runtime_coherence.py` имеет не менее 100 строк
  запаса, common primitives имеют единственного owner без forwarding façade,
  tests запускаются через `scripts/run_tests.sh`, strict OpenSpec validation
  и independent review обязательны;
- RLIMIT tests используют hermetic child Python trampoline без зависимости
  от ambient hard/infinity и без `preexec_fn`;
- independent review запускается в `workspace-write` sandbox, но остаётся
  source-read-only: tests пишут только temp/cache/evidence, а pre/post source
  diff неизменен. Одна exact five-module команда с
  `HERMES_TEST_FILE_RETRIES=0` должна успешно завершиться два раза подряд
  без retry/`FLAKY`; fingerprints обоих запусков идентичны. `0 collected`,
  environment blocker или один успешный run не являются acceptance.

Гейты доставки после этого material delta:

1. Независимый run
   `20260729T224514Z-kanban-remediation-independent-review` зафиксирован как
   `BLOCK`, несмотря на два зелёных exact four-suite runs. Он исторически
   переоткрыл 19.4, 19.6, 19.7 и 20.2; текущая truth state приведена ниже.
2. Material sealed-content baseline из раздела 21.x явно одобрен Русланом
   2026-07-30; approval разрешило только remediation implementation/tests и
   repo-local/temp-only verification в task-owned worktree.
3. Sealed content bundle, exception-safe resource owner, содержательный
   reusable test harness и adversarial nested-mutation tests реализованы.
4. Author-local exact four-suite command два раза подряд завершён
   `140 passed, 0 failed`; это evidence, но не independent acceptance.
5. Live-действия, commit, push или PR этим approval и author evidence не
   разрешены.
6. Выполнить два последовательных exact five-module runs с retries `0` в
   workspace-write source-read-only review sandbox, без `FLAKY`, с
   идентичными fingerprints; выполнить strict checks и получить новый
   independent review. До accepted review без `BLOCK`
   запрещены commit, push и PR.
7. Только после accepted review разрешаются commit/push/task-owned PR и
   обычный repository lifecycle; merge не открывает live gate.
8. После merge отдельно запросить и выполнить только `prepare` dry-run; он
   доказывает plan/hashes, но не import-origin.
9. После проверки dry-run evidence отдельно запросить `prepare --apply`; здесь
   впервые появляется import-origin evidence.
10. После проверки v3 snapshot отдельно запросить `switch` dry-run с повторной
   проверкой происхождения импортов.
11. После проверки switch dry-run отдельно запросить `switch --apply` с
   повторным import-origin audit.
12. Только после repository lifecycle и отдельного exact разрешения выполнить
   current-connector
   replacement и bounded smoke: `initialize`, `tools/list`,
   `kanban_sync_external_task` dry-run без DB writes.
13. Live rollout, wrapper/restart/process replacement и DB остаются отдельным
   exact gate. Ни planning approval, tests, accepted review, commit, push, PR
   или merge не разрешают global Hermes symlink, Hermes/Gurra restart,
   service changes, DB writes или Kanban mutation.

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
  ни одного примитива записи; filesystem before/after oracle покрывает все три
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
- [Bootstrap получает отдельную atomic policy] → switch использует общий
  runtime validator/atomic primitive; snapshot-only rollback имеет отдельный
  loader, но тот же atomic primitive и exact guards.
- [State root частично создан после ошибки] → не очищать автоматически;
  сохранять exact evidence и закрывать повторный apply на existing root.
- [Schema v1 migration расширяет риск] → не поддерживать v1, так как live
  snapshots отсутствуют; existing state root всегда требует stop/replan.
- [Common primitives перемещены, но ownership не изменился] → выделить
  единственного common owner для path/Git/venv primitives, удалить state
  forwarding re-export façade и измерять минимум 100 строк запаса у
  `runtime_coherence.py`.
- [Copied candidate venv затеняет target source через старый site-packages] →
  canonical `source-cwd-nofile-v2` wrapper, `-I -S -B`, exact environment и guarded
  import-origin preflight до snapshot и повторная проверка на switch; без
  network, `pip`, editable install или `.pth`.
- [Исправление ломает existing rollback oracle] → schema v2 остаётся readable для
  snapshot-only rollback exact bytes/mode; rollback не зависит от source repo,
  candidate runtime/venv/imports и работает при missing/corrupt/dirty candidate.
- [Новый wrapper parser принимает неоднозначный legacy wrapper] →
  allow-listed exact templates и rejection comments-only, missing exec, extra
  commands, redirects и control operators до примитивов записи.
- [Preflight читает real HOME или выполняет import side effects] → synthetic
  HOME/HERMES_HOME, `find_spec` для `hermes_cli.main`, guards до dedicated
  server import и traps для file/network/process/DB; stderr sanitization.
- [Python audit/blocklist пропускает low-level или native syscall] →
  обязательный exact `/usr/bin/bwrap` до candidate `exec`, пустой mount
  namespace, read-only binds, PID/network/user/IPC/UTS/cgroup isolation и
  host-canary acceptance; Python policy остаётся только вторым слоем.
- [Directory FD не защищает nested in-place mutation] → не bind mutable
  backing tree; каждый executable/importable regular file копируется в
  sealed memfd, а anchors/digests строятся из тех же captured bytes.
- [Capture обещает bytes до начала наблюдения] → явно ограничить контракт:
  exact captured verified bytes от успешного anchor construction до
  `exec`/`import`; неполный или конкурентно изменяемый сбор манифеста
  завершается fail-closed.
- [Candidate сам объявляет interpreter/stdlib доверенными] → parent строит
  required runtime closure и sealed manifest; exact child match не заменяет
  сбор, выполненный родительским процессом.
- [Nested mutate→import/effect→restore скрывает подмену] → `bwrap` получает
  только sealed regular files и manifest topology; forged matching child
  evidence не влияет на selected bytes, host-canary oracle обязателен.
- [Ошибка capture оставляет FDs] → немедленная регистрация каждого
  `open`/`memfd`, current-FD cleanup в `_data_fd`, общий partial-bundle
  cleanup и structured cleanup failures; failure injection на каждой стадии.
- [Bubblewrap baseline probe ошибочно объявлен полным] → честно ограничить
  probe базовыми namespaces/mounts; полный production invocation со всеми
  sealed content/data binds является authoritative и fail-closed.
- [Новая grammar незаметно ломает исторический v2 rollback] → статический
  sanitized historical golden без raw host prefix во всех четырёх files;
  provenance хранит source class/hash, но не source literal, payload hashes и
  snapshot-only semantics неизменны.
- [Разгрузка rollout test является thin forwarding] → существующий support
  становится содержательным owner Git/layout/oracle harness; gates
  rollout `<=850`, support `<400`, behavior unchanged и сохранение existing
  candidate/snapshot, symlink stable wrapper и future parent symlink cases.
- [Read-only review sandbox не даёт запустить tests] → независимая validation
  использует workspace-write для temp/cache/evidence при source-read-only
  review и требует два последовательных exact five-module run с retries `0`,
  без `FLAKY`, с идентичными fingerprints.

## План доставки

1. Сохранить закрытыми выполненные PR #15 на
   `062f2f0f1f6947830d1b222a3ef470e145a7c34d` и PR #16 на
   `9fcd66651768e3cf220d5cd501efbec5ae3e2550`.
2. Bootstrap-helper PR уже доставлен; live bootstrap/prepare/switch evidence
   сохранён в tasks.
3. Rollout target откатан к baseline wrapper. Independent review
   `20260729T224514Z-kanban-remediation-independent-review` зафиксирован как
   `BLOCK`; два зелёных runs сохранены как evidence, не acceptance.
4. Tasks 19.4, 19.6, 19.7, 20.2 и 21.2–21.5 закрыты по approval
   2026-07-30 и фактической repo-local реализации; сохранить открытыми 16.7,
   18.8, 19.8, 19.9, 20.7, 20.8 и 21.6–21.8.
5. Исторический author exact four-suite выполнен два раза подряд:
   `140 passed, 0 failed`; это evidence старого snapshot, не текущая
   acceptance.
6. В workspace-write/source-read-only review sandbox два раза подряд
   запустить exact five-module команду через `scripts/run_tests.sh` с
   `HERMES_TEST_FILE_RETRIES=0`, проверить отсутствие retry/`FLAKY`,
   идентичные fingerprints, acceptance/bypass matrix, отсутствие FD leaks,
   line counts и `runtime_coherence.py <=900`, строгую проверку OpenSpec,
   `git diff --check` и exact область diff.
7. Получить independent review без `BLOCK`. Только после accepted review
   разрешены commit, push и task-owned PR; затем обычный PR/merge lifecycle.
8. После merge и отдельных exact approvals пройти `prepare` dry-run/apply и `switch`
   dry-run/apply как четыре раздельных gates.
9. Current-connector replacement и bounded `initialize`/`tools-list`/
   dry-run-sync smoke выполняются только после repository lifecycle и без DB
   writes.
10. При провале использовать schema v2/v3 snapshot-only rollback; не
   удалять baseline/target/snapshots и не менять global Hermes symlink,
   Hermes/Gurra, services, connector config или live DB.

Будущий rollback, если отдельный rollout будет одобрен, ограничивается
`rollback --apply` по exact snapshot, возвратом предыдущего standalone MCP
process и повторным bounded smoke. Candidate/snapshot сохраняются как
evidence; автоматического cleanup нет.

### 22. Двухфазный bounded inventory отделён от sealed acquisition

Первый проход выполняет только descriptor-relative inventory с
`O_NOFOLLOW`. Он удерживает одновременно не больше малого фиксированного
числа временных FD, строит bounded topology, identities, digests и exact ELF
dependency plan, но не создаёт content memfd. После inventory resource
planner обязан доказать достаточность всех лимитов. Только затем второй
проход открывает каждый объект descriptor-relative, захватывает bytes,
создаёт sealed data object и повторно сверяет topology, identity и digest с
inventory. Любое расхождение или превышение границы завершает операцию
fail-closed с structured cleanup.

Production invocation получает только bytes второго прохода, которые
совпали с inventory и были sealed до handoff. Если platform не позволяет
безопасно повторно открыть объект или доказать identity/digest, объект и весь
invocation отклоняются. Однофазное создание content memfd отклонено:
resource failure тогда возникал бы после partial sealed acquisition.

### 23. ELF dependency plan моделирует GNU/Linux semantics точно

ELF parser хранит `DT_RPATH` и `DT_RUNPATH` раздельно. Resolver моделирует
GNU/Linux loader search order: `RUNPATH` defining object supersedes его
`RPATH`, применяется к direct dependencies и не наследуется; legacy
`RPATH` наследуется по dependency chain в точных условиях отсутствия
`RUNPATH`. Default directories используются только после корректного
применения этих правил.

`$ORIGIN`, `$LIB` и `$PLATFORM` раскрываются детерминированно из exact
runtime/platform facts. Если exact значение недоступно, token отклоняется до
capture. Relative, empty и unsafe entries, path escape, slash/`NUL`/escape в
`DT_NEEDED` запрещены. Dynamic segment bounded, его размер кратен entry size,
`DT_NULL` обязан находиться внутри segment; каждый string offset и
завершающий `NUL` проверяется внутри bounded string table.

Tests используют независимые literal handcrafted ELF bytes и ожидаемые
dependency plans; production parser не строит fixture или oracle. Mutations
покрывают `RPATH`, `RUNPATH`, inheritance, tokens, alignment, missing
`DT_NULL`, invalid string bounds и unsafe `DT_NEEDED`.

### 24. Resource planner предшествует acquisition и invocation

Planner получает current open FD count и считает content entries плюс
manifest, loader, `bwrap`, libraries, harness, anchors, probe/prod args и
явный subprocess/`bwrap` safety reserve. Сумма сравнивается с текущим finite
soft `RLIMIT_NOFILE`; учитываются `pass_fds`, platform и `bwrap`
constraints.

Полный размер exec `argv` и environment в bytes сравнивается с
`SC_ARG_MAX` за вычетом именованного safety margin. Payload для
`bwrap --args` не считается подчинённым `ARG_MAX`, но имеет отдельный явный
constant/configurable maximum и проверяется до создания его memfd и до
invocation.

Любой budget failure происходит до первого content memfd и до subprocess.
Structured cleanup сохраняет primary failure, а cleanup failure добавляет
отдельно. Independent minimal real capture исторически удержал `1238` FD при
topology около `110908` bytes, поэтому прежняя проверка только
`RLIMIT_NOFILE >= 64` не является capacity proof.

### 25. Minor remediation: canonical invocation является budget owner

Новый independent review обнаружил три несоответствия уже одобренному
контракту 22.x: trusted ELF root не перепроверялся после symlink hop, actual
probe loader argv не проверялся против `SC_ARG_MAX`, а pre-acquisition plan
описывал placeholder probe/production и только file binds. Verdict сохранён
как historical `BLOCK`; это minor remediation без изменения scope,
observable requirements, architecture direction, environment или delivery
gates, поэтому нового approval не требуется.

Единственный immutable canonical invocation spec теперь одновременно задаёт
probe и production bwrap args, loader/preload argv, полную topology
directories/files/symlinks/perms, harness/anchors и именованные FD roles.
Inventory строит этот spec до первого content memfd. Для каждого FD role
pre-acquisition render использует строку из максимальной законной десятичной
ширины `finite RLIMIT_NOFILE - 1`. Это консервативная верхняя граница:
каждый actual FD неотрицателен и строго меньше soft limit, поэтому его
десятичное представление не длиннее symbolic render. После acquisition тот
же spec рендерится с actual role→FD map; код утверждает actual args/exec
bytes `<=` prevalidated bound и повторно проверяет bwrap args cap,
`SC_ARG_MAX`, current/peak FD и `pass_fds` перед соответствующим
args-memfd/subprocess. Поэтому cap failure детерминированно происходит до
content acquisition даже без преждевременного назначения actual FD numbers.

External ELF inventory после каждого absolute или relative symlink hop
нормализует оставшийся путь и заново проверяет containment в injectable
trusted roots. Escape, dangling target и cycle завершаются fail closed;
системные `/usr`, `/lib`, `/lib64` не изменяются, tests используют только
временные корневые каталоги.

## Открытые вопросы

Sealed-content material baseline был одобрен 2026-07-30. Новый independent
review вернул `BLOCK` по трём несоответствиям существующим requirements;
verdict сохранён как historical evidence, а minor remediation 25 выполняется
в рамках прежнего approval. Author exact validation и новая independent
validation/review остаются открытыми до фактического прохождения; delivery
truth также не закрывается. Commit/push/PR до accepted review запрещены.
Scope live действий не расширяется; live
rollout/wrapper/restart/process replacement/DB требуют отдельного exact
разрешения.

### 26. Minor remediation: acquisition peak и final handoff authoritative

Latest independent review `BLOCK` зафиксирован как historical evidence:
предыдущий planner учитывал steady-state content/fixed reserve, но не полный
recursive acquisition lifecycle; final `pass_fds` проверялся до создания
args/harness/anchors memfd; constants/base policy имели более одного owner;
role map и symlink oracle были неполными. Это implementation gaps требований
22.x/23.x без изменения scope, observable behavior, architecture direction,
environment или delivery gates, поэтому новый approval не требуется.

`InventoryPlan.acquisition_temporary_fds` задаёт отдельный строгий
консервативный reserve: `MAX_DIRECTORY_DEPTH + 1` одновременно удерживаемых
directory descriptors плюс source FD и создаваемый sealed memfd. Resource
plan включает этот named reserve до первого content memfd; общий magic reserve
для него не переиспользуется.

Непосредственно после создания probe args FD и production harness/anchors/args
FD parent локально фиксирует exact final `pass_fds`, сверяет его с
authoritative bundle ownership, проверяет порядок, уникальность, открытость,
finite soft limit, current open count и subprocess/bwrap peak reserve и затем
сразу вызывает subprocess с тем же tuple. Любая ошибка становится structured
fail-closed и subprocess не вызывается.

Единственный owner `BWRAP`, `SANDBOX_RUNTIME`, `SANDBOX_ENV`, base и
политика production — модуль invocation. Точный обязательный порядок ролей:

- probe: `loader`, затем `library:0..N`, `bwrap`, `probe_args`;
- production: ordered `file:<destination>` из canonical inventory, затем
  `harness`, `anchors`, `loader`, `library:0..N`, `bwrap`,
  `production_args`.

Missing, extra или reordered map отклоняется `ResourceBudgetError` до render
и subprocess. Temp-only literal symlink oracle закрепляет valid relative
multi-hop и fail-closed absolute escape, relative escape, dangling и cycle,
не меняя `/usr`, `/lib` или `/lib64`.

### 27. Minor remediation: topology preflight и acquisition depth

Новый independent review `BLOCK` выявил P1 implementation gap существующего
контракта: named acquisition reserve выводился из `MAX_DIRECTORY_DEPTH`, но
recursive sealed acquisition не применял этот предел. Mutation после
inventory могла добавить слишком глубокую ветку; лексически ранние files
успевали создать content memfd до итоговой сверки manifest.

Непосредственно в начале `_BundleBuilder.build`, до `_walk_tree` и первого
content memfd, тот же canonical `InventoryBuilder.tree` повторяет
topology-only проход с теми же roots, exclusions и depth policy, не открывая
и не читая regular-file content. Покрываемая topology path/kind/mode/symlink
target должна совпасть с approved `InventoryPlan`; ошибка inventory либо
расхождение возвращаются как structured `SandboxError`, пока FD owner пуст.

После preflight `_walk_directory` независимо получает текущую depth и
импортирует `MAX_DIRECTORY_DEPTH` из единственного inventory owner. Перед
открытием следующего directory на запрещённой глубине traversal завершается
fail closed. Этот guard остаётся load-bearing для mutation после preflight и
сохраняет рассчитанный temporary FD peak; общий planner, sealing и final
handoff не меняются.

### 28. MATERIAL DELTA: versioned canonical wrapper владеет NOFILE capacity

Фактический owner exact wrapper grammar/layout остаётся
`scripts/hermes_kanban_mcp_runtime_coherence.py`; владельцем rollout
schema/snapshot/hash/transition state остаётся
`scripts/hermes_kanban_mcp_rollout_state.py`. Изменять смысл
`source-cwd-v1` нельзя: этот contract уже записан в исторические schema-v3
snapshots. Поэтому generator переходит на новый versioned canonical grammar
kind `source-cwd-nofile-v2`, а `source-cwd-v1` и schema-v2 grammar остаются
parse/rollback-only и больше не выпускаются generator.

Exact generated layout нового kind:

1. `#!/bin/bash`;
2. `set -euo pipefail`;
3. exact allow-listed exports в существующем порядке;
4. ровно одна строка `ulimit -S -n 4096`;
5. exact `cd -- <target-runtime>`;
6. exact `exec <target>/venv/bin/python -m hermes_cli.main ...`.

Таким образом limit задаётся после `set -euo pipefail` и exports, но до
`cd --` и `exec` Python. Bash `set -e` обеспечивает nonzero
fail-before-Python, если hard limit ниже `4096`; реализация не добавляет
helper, не повышает hard limit и никогда не устанавливает soft limit в
`unlimited`. Parser нового grammar отклоняет missing, malformed, duplicate,
wrong-value и unlimited `ulimit`, а также любое иное расположение строки.
Существующая явная диагностика shell достаточна; новый stderr protocol не
вводится.

Выбранный owner лучше альтернатив:

- Python self-raise отклонён: candidate Python уже запущен, то есть capacity
  устанавливает слишком поздний слой;
- systemd/`prlimit`/внешний launcher отклонены: они переносят ownership во
  внешний live/service слой, добавляют root/service coupling и не являются
  частью deterministic wrapper snapshot.

Rollback восстанавливает exact historical `wrapper.before` bytes и mode.
Ни parsing старого `source-cwd-v1`, ни schema-v2 snapshot-only rollback не
переписывают wrapper и не добавляют NOFILE line. Existing resource planner
продолжает проверять фактический finite soft limit и fail-closed при
недостаточной вместимости; `4096` не является обходом planner.

Dry-run строит новый deterministic `wrapper.after`, сообщает grammar kind,
exact planned soft limit `4096` и planned wrapper SHA-256, но не пишет
snapshot/runtime/wrapper. Apply и switch повторно связывают те же
kind/limit/hash evidence перед mutation. Planning, code, review, PR или merge
не выполняют live action.

### 29. Remediation после BLOCK: единый creation/switch contract и hash gate

Independent review дельты 26.x завершился `BLOCK`; 26.3–26.5 остаются
historical implementation evidence и superseded текущим design. Новый
baseline одобрен exact фразой пользователя и реализован только repo-local в
рамках 27.3–27.4; 27.5+ остаются открытыми.

Новый bootstrap и rollout используют один путь создания:

1. канонический генератор строит `wrapper.after` типа
   `source-cwd-nofile-v2`;
2. разобранная обёртка даёт производный запланированный мягкий лимит `4096`;
3. манифест сохраняет существующую форму с `schema_version=3`,
   `snapshot_kind=bootstrap|rollout`,
   `wrapper_contract=source-cwd-nofile-v2` и after SHA-256;
4. отдельный plan digest не создаётся;
5. `--expected-wrapper-after-sha256` связывает operator-approved dry-run
   evidence с каждым apply;
6. точный загрузчик switch повторно проверяет тип, контракт, разобранный
   лимит, хеш манифеста и фактические байты непосредственно перед дальнейшим
   preflight.

Dry-run только сообщает after SHA-256 и planned soft limit. Для
`bootstrap-prepare --apply`, `prepare --apply` и `switch --apply` missing или
mismatch expected after SHA MUST завершать вызов до первого managed write,
candidate preflight и `os.replace`. Rollback CLI и его snapshot-only
семантика не меняются.

Switch eligibility является строгой: любой `schema_version!=3` и historical
schema-v3 `source-cwd-v1` отклоняются до preflight/mutation. Отдельный
rollback loader всё ещё может использовать их только как immutable exact
bytes/mode input. Переписывать manifest/wrapper на месте или мигрировать
snapshot запрещено.

Основные владельцы новой policy:

- `scripts/hermes_kanban_mcp_rollout_state.py` — схема/тип/контракт,
  манифест/хеш и границы точных загрузчиков switch/rollback;
- `scripts/hermes_kanban_mcp_rollout.py` — CLI guard и порядок dry-run/apply;
- `scripts/hermes_kanban_mcp_runtime_coherence.py` — только canonical
  generator/parser и неизбежная exact contract/limit проверка.

`runtime_coherence.py` уже имеет `897/900` строк, поэтому новая state/CLI
policy туда не добавляется. Если минимальная правка не помещается, extraction
разрешена только в существующий
`scripts/hermes_kanban_mcp_rollout_common.py` как substantive owner; новый
thin wrapper запрещён. Тестовый reusable код аналогично может переходить
только в существующий `tests/scripts/hermes_kanban_mcp_test_support.py`.

RLIMIT tests не используют состояние runner. Child Python trampoline сам
задаёт finite soft/hard limits в дочернем процессе, затем запускает exact
wrapper; ambient hard limit/infinity и `preexec_fn` не участвуют.

Финальная acceptance — одна exact команда с пятью modules, два раза подряд
на одном final snapshot:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/scripts/test_hermes_kanban_mcp_bootstrap.py \
  tests/scripts/test_hermes_kanban_mcp_rollout.py \
  tests/scripts/test_hermes_kanban_mcp_runtime_coherence.py \
  tests/scripts/test_hermes_kanban_mcp_runtime_sandbox.py \
  tests/scripts/test_hermes_kanban_mcp_rollout_state.py
```

Оба запуска обязаны пройти без edits/retry/`FLAKY`, с идентичными pre/post
fingerprints. Только затем выполняются independent source-read-only review и
non-live PR lifecycle. Live dry-run/apply/process gates остаются прежними
отдельными approvals.
