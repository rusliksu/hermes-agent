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

## 7. Сохранённый high-level live gate

- [ ] 7.1 После merge bootstrap-helper PR отдельно запросить одобрение exact
  bootstrap dry-run и зафиксировать export rollback target; merge PR не
  считается live approval.
- [ ] 7.2 Только после последовательных gated
  bootstrap/prepare/switch steps точечно заменить standalone MCP process; не
  менять глобальный Hermes symlink, не перезапускать Hermes/Gurra и не
  переносить dirty Telegram patch.

## 8. Выполненный гейт material delta helper PR #16

- [x] 8.1 Зафиксировать явное одобрение material delta: отдельный helper PR
  без live rollout; текущий запуск — только planning/OpenSpec.
- [x] 8.2 Перед реализацией повторно подтвердить task-owned worktree/branch,
  exact base `062f2f0f1f6947830d1b222a3ef470e145a7c34d`, чистый status и
  отсутствие material изменения standalone layout; при расхождении
  остановиться и оформить новый delta.

## 9. Выполненный helper PR #16

- [x] 9.1 Добавить один `scripts/hermes_kanban_mcp_rollout.py` на Python
  stdlib с argparse-командами `prepare`, `switch`, `rollback`; отсутствие
  `--apply` должно быть единственным dry-run default.
- [x] 9.2 Реализовать общую read-only validation/plan фазу: полные Git
  SHA/SHA-256, абсолютные canonical paths, root containment, запрет symlink и
  broad targets, exact current runtime/wrapper preconditions и JSON plan без
  примитивов записи.
- [x] 9.3 Реализовать `prepare --apply`: exact detached Git worktree
  `<runtime-root>/hermes-kanban-mcp-<FULL_GIT_SHA>`, перенос только указанного
  `.venv`/`venv`, проверка exact HEAD и tracked cleanliness без
  `reset`/`clean`/delete.
- [x] 9.4 Создать exclusive deterministic snapshot
  `<state-root>/snapshots/<CURRENT_FULL_SHA>-to-<CANDIDATE_FULL_SHA>`
  только с `manifest.json`, `wrapper.before`, `wrapper.after`, owner-only
  modes и повторной проверкой hashes; stable wrapper на prepare не менять.
- [x] 9.5 Реализовать `switch --apply` с повторной проверкой manifest,
  snapshot, candidate, venv и expected current wrapper SHA-256; выполнить
  только same-directory temp write, file fsync, один `os.replace` stable
  wrapper и directory fsync; exact temp удалять в `finally` только до
  успешной замены, а post-replace fsync/verification failure возвращать с
  `replacement_applied=true`, expected installed SHA-256 и
  `inspect/rollback`.
- [x] 9.6 Реализовать `rollback --apply` с guard текущего wrapper против
  `wrapper_after_sha256` и явного expected SHA-256; атомарно восстановить
  byte-identical `wrapper.before` и executable mode без удаления candidate
  или snapshot.
- [x] 9.7 Подтвердить в code review, что helper не читает env/credentials/
  tokens/sessions/DB, не управляет processes/services, не использует broad
  `rm`, `rmtree`, `reset`, `clean`, globs и не меняет global Hermes symlink.

## 10. Выполненные temp-only tests и проверки PR #16

- [x] 10.1 Добавить
  `tests/scripts/test_hermes_kanban_mcp_rollout.py` с временным Git repo
  current/candidate commits, fake venv/interpreter, runtime/state roots и
  обычным исполняемым stable wrapper.
- [x] 10.2 Для `prepare`, `switch`, `rollback` проверить default dry-run
  полным filesystem before/after oracle и отсутствием write primitive calls.
- [x] 10.3 Проверить temp-only happy path end-to-end: prepare создаёт exact
  candidate/snapshot без switch, switch устанавливает exact
  `wrapper.after`, rollback восстанавливает byte-identical
  `wrapper.before` и mode.
- [x] 10.4 Проверить fail-closed cases: stale expected wrapper/runtime SHA,
  изменённые manifest/snapshot/candidate/venv, относительные, выходящие за
  границы, слишком широкие пути и пути с symlink, существующий candidate и
  искусственно вызванный сбой рабочего дерева, снимка или замены;
  stable wrapper не меняется до `os.replace`; отдельно проверить exact temp
  очистку, `stderr` состояния `applied-state` после замены, подстановки пути
  `manifest`, валидного по схеме, snapshot modes, HEAD/dirty tamper, future parent symlink и
  partial candidate/venv/snapshot evidence без автоматического cleanup.
- [x] 10.5 Запустить focused tests только через
  `scripts/run_tests.sh tests/scripts/test_hermes_kanban_mcp_rollout.py` и
  подтвердить, что все artifacts находятся под test temp directory.
- [x] 10.6 Проверить diff helper PR: только exact helper/test/OpenSpec files,
  без production modules, dependencies, DB migration, live config/wrapper,
  runtime artifacts, services или process changes.
- [x] 10.7 Выполнить
  `/home/openclaw/.local/bin/openspec validate
  expose-external-sync-on-kanban-mcp --strict --no-interactive` и
  `git diff --check`.
- [x] 10.8 Получить независимое review helper реализации без `BLOCK`; только
  после зелёных tests/review создать отдельный task-owned PR без live
  rollout.

## 11. Планирование существенного изменения для `PR` вспомогательного инструмента `bootstrap`

- [x] 11.1 Зафиксировать предоставленный evidence без повторного чтения live
  пути: экспорт вне `Git`, `SHA` источника, кандидата и базы слияния, хэши `wrapper/venv`
  и отсутствие dedicated state root/baseline/target.
- [x] 11.2 Зафиксировать material divergence: schema v1 не представляет
  export→Git переход с одинаковым source SHA; перейти на schema v2 variants и
  unified runtime/state root без второй switch/rollback policy.
- [x] 11.3 Обновить на русском proposal, design, capability spec, tasks и
  README этого change; сохранить выполненные PR #15/#16 tasks закрытыми.
- [x] 11.4 Выполнить `openspec status`, strict validation и
  `git diff --check`; implementation files/tests не менять.

## 12. Реализация отдельного bootstrap-helper PR

Baseline реализации явно одобрен пользователем 2026-07-28.

- [x] 12.1 Добавить в `scripts/hermes_kanban_mcp_rollout.py` exact CLI
  `bootstrap-prepare` с обязательными source repo, absent state root, export
  runtime/manifest, source commit, venv/interpreter и stable wrapper evidence;
  экспортный манифест проверять как обычный файл без символьных ссылок строго
  внутри экспортированной среды и разбирать как непустые строки `key=value` в
  `UTF-8`. Запретить пустой или повторяющийся ключ, повреждённую строку и
  `NUL`; потребовать `source_commit` ровно один раз как полный `Git SHA`,
  равный явно ожидаемому значению. Неизвестные ключи разрешать, но их значения
  не выводить и не переносить в снимок. Пробный запуск печатает наблюдаемый
  `SHA-256` сырых байтов, а `--apply` требует его явное ожидаемое значение.
- [x] 12.2 Реализовать `bootstrap-prepare --apply`: exclusive state root mode
  `0700` при validated existing parent, exact detached baseline worktree,
  copy-only выбранного venv и stable wrapper unchanged.
- [x] 12.3 Вынести schema/snapshot/transition ownership в
  `scripts/hermes_kanban_mcp_rollout_state.py`; перевести новые bootstrap и
  обычные rollout snapshots на exact schema v2 без schema v1 compatibility.
- [x] 12.4 Перевести существующие `switch/rollback` на один общий schema v2
  loader/validator и существующий atomic replacement primitive; для
  bootstrap повторно проверять export manifest/venv и baseline exact
  `HEAD`, чистоту отслеживаемых файлов и `venv`.
- [x] 12.5 Разрешить только exact equality `runtime-root == state-root` для
  unified layout; сохранить запрет разных nested roots, symlink/broad/path
  escape и exact temp unlink policy.
- [x] 12.6 Подтвердить stdlib-only implementation без dependencies,
  общей системы конфигурации, библиотеки схем, второй политики манифеста,
  операций с процессами, сервисами, сетью и БД, широкого удаления, `reset`,
  `clean`, `rmtree` или шаблонов путей.

## 13. Тесты только во временной среде для `PR` вспомогательного инструмента `bootstrap`

- [x] 13.1 Добавить
  `tests/scripts/test_hermes_kanban_mcp_bootstrap.py` с non-Git export,
  `manifest.txt` в формате `UTF-8` строк `key=value` с фактическими ключами
  `source_commit`, `deployed_utc`, `python_version`, `mcp_version`, `command`,
  расходящимися коммитами источника и цели, фиктивным `venv` и `wrapper`
  только во временном каталоге.
- [x] 13.2 Проверить `bootstrap-prepare` dry-run полным filesystem oracle и
  запретом write primitives; state root до apply отсутствует.
- [x] 13.3 Проверить apply: exact state root mode `0700`, baseline detached
  HEAD/tracked cleanliness, copy-only venv, schema v2 bootstrap snapshot и
  неизменный стабильный `wrapper`.
- [x] 13.4 Проверить закрытие при дубликате ключа, повреждённой или пустой
  строке, пустом ключе, `NUL`, ошибке `UTF-8`, отсутствующем/несовпадающем
  `source_commit`, изменении сырых байтов/`SHA-256` и символьной ссылке
  манифеста; отдельно проверить разрешённый неизвестный ключ без вывода или
  копирования его значения. Сохранить проверки ровно одной замены `wrapper`,
  `venv/interpreter`, `path/root`, существующего/частичного состояния и
  отсутствия автоматической очистки.
- [x] 13.5 Проверить bootstrap switch/rollback end-to-end через общий
  consumer: repeated export/baseline evidence, wrapper export→baseline и
  побайтово идентичный откат `baseline→export`.
- [x] 13.6 Обновить
  `tests/scripts/test_hermes_kanban_mcp_rollout.py` для schema v2 regression и
  unified-root baseline→target обычного prepare/switch/rollback.
- [x] 13.7 Удержать каждый source/test file ниже 1000 строк и не добавлять
  tests, читающие source text.
- [x] 13.8 Запустить оба focused test files только через
  `scripts/run_tests.sh`; подтвердить, что artifacts не выходят из temp
  directories.
- [x] 13.9 Закрыть два `P3` validation gap из независимого ревью
  `APPROVE WITH RISKS` без blockers: проверить настоящий schema v1 manifest
  по контракту base `9fcd666`, а также отклонение отсутствующего state root
  с symlink parent и broad target до write primitives. Production source не
  потребовал изменений; focused suite — `73 passed`, `py_compile` и
  `git diff --check` зелёные, максимальный размер файла — `978` строк.

## 14. Review и отдельный bootstrap-helper PR

- [x] 14.1 Проверить exact diff scope: два helper source files, два helper
  test files и этот OpenSpec change; без production modules, dependencies,
  DB migration, live config/wrapper/runtime/state или service artifacts.
- [x] 14.2 Выполнить strict OpenSpec validation, `git diff --check` и
  независимое code review без `BLOCK`.
- [x] 14.3 Создать отдельный task-owned bootstrap-helper PR без live effects;
  не выполнять commit/push/PR в planning run.
- [x] 14.4 Зафиксировать в PR и handoff: merge bootstrap-helper PR не
  разрешает никакой live apply.

## 15. Новый точный live gate после merge bootstrap-helper PR

- [x] 15.1 Точный выделенный каталог состояния
  `/home/openclaw/.hermes/mcp-rollout-state` отсутствует, поэтому артефакты
  `schema v1` внутри отсутствуют; доказательство — запуск
  `20260728T211855Z-kanban-bootstrap-readonly-preflight`.
- [x] 15.2 Один пробный запуск `bootstrap-prepare` завершён с кодом выхода `0`,
  операции не выполнялись (`0`, `apply=false`); наблюдаемый SHA-256 манифеста
  `caf998929f3778e37bd2b516821aad5ab2dd7c7cf3f43a15147eca97a2cbf616`;
  базовый каталог
  `/home/openclaw/.hermes/mcp-rollout-state/hermes-kanban-mcp-6f8738dc308f909bf1735883344f2fcc12f3cbcd`;
  снимок
  `/home/openclaw/.hermes/mcp-rollout-state/snapshots/bootstrap-6f8738dc308f909bf1735883344f2fcc12f3cbcd`;
  SHA-256 `wrapper` до `20e2cb13...` и запланированный после `17052c7d...`;
  контрольные снимки до и после совпали; доказательство — запуск
  `20260728T212721Z-kanban-bootstrap-exact-dry-run`.
- [x] 15.3 Один одобренный `bootstrap-prepare --apply` завершён с `exit 0`,
  `result=prepared`; запуск
  `20260729T065556Z-apply-kanban-bootstrap-baseline`. Каталог состояния
  `/home/openclaw/.hermes/mcp-rollout-state` имеет mode `0700`; baseline
  `/home/openclaw/.hermes/mcp-rollout-state/hermes-kanban-mcp-6f8738dc308f909bf1735883344f2fcc12f3cbcd`
  имеет detached HEAD на exact source SHA, tracked-clean, SHA/mode
  интерпретатора venv совпали. Snapshot
  `/home/openclaw/.hermes/mcp-rollout-state/snapshots/bootstrap-6f8738dc308f909bf1735883344f2fcc12f3cbcd`:
  `schema_version=2`, `snapshot_kind=bootstrap`, каталог `0700`, три файла
  `0600`, SHA-256 манифеста
  `b3206d44bf1dd34988223725aff539408734d45eb2334908892a48af42c2309d`.
  Stable wrapper, export manifest и venv неизменны; SHA-256 wrapper
  `20e2cb13...`, SHA-256 манифеста `caf99892...`, запланированный SHA-256 `wrapper.after`
  `17052c7d...`. Switch/restart/deploy/process/smoke не выполнялись.
- [x] 15.4 Один одобренный bootstrap `switch --apply` без повторной попытки
  завершён с `exit 0`, `result=switched`; запуск
  `20260729T081940Z-apply-kanban-bootstrap-switch`. Изменён только
  `/home/openclaw/.hermes/mcp/hermes-kanban/run.sh`. Новый wrapper является
  каноническим обычным файлом без символьной ссылки с mode `0755` и SHA-256
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`,
  побайтово совпадает со snapshot `wrapper.after`, содержит одну ссылку на
  baseline и ни одной ссылки на export; контракт сохранён. Oracle отката
  `wrapper.before` имеет SHA-256
  `20e2cb13c7162a833fea32f79aea59591e759c4ca2ab181e0c0a12f0e3add089`.
  Baseline, snapshot и export не изменились, временные helper-файлы
  отсутствуют. Restart, deploy, process smoke, DB и Kanban sync не
  выполнялись.
- [x] 15.5 Выполнить обычный `prepare` baseline→target сначала dry-run, затем
  только после отдельного gate `prepare --apply`; проверить target и rollout
  снимок со `schema_version=2`.
  Частичная evidence от 2026-07-29: модель `gpt-5.6-sol`, запуск
  `20260729T083434Z-hermes-prepare-dryrun`; один ordinary `prepare` dry-run
  без `--apply` завершён с `exit 0`. Candidate target —
  `30500cf973a40bb0918d33eb0476c1025e08ac0f`: между ним и current main
  `1e2b27126e4500edc3ad40f733575f9122b9d7c4` менялся только
  `openspec/.../tasks.md`, non-OpenSpec production diff отсутствует и деревья
  одинаковы. Запланированы candidate
  `/home/openclaw/.hermes/mcp-rollout-state/hermes-kanban-mcp-30500cf973a40bb0918d33eb0476c1025e08ac0f`
  и snapshot
  `/home/openclaw/.hermes/mcp-rollout-state/snapshots/6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`;
  запланированный SHA-256 `wrapper.after` —
  `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`.
  State oracle до и после одинаков:
  `ec357d753a678b6f37010f4ee98b5966e529bc61a6794feb2d0f738236417fb1`
  (`total=10093`, `files=8744`, `dirs=1345`, `symlinks=4`, top-level `2`).
  Wrapper не изменился: обычный файл `0755`, `286` bytes, SHA-256
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`;
  ссылки `baseline=1`, `candidate=0`, `export=0`. Baseline — exact HEAD
  `6f8738dc308f909bf1735883344f2fcc12f3cbcd`, без изменений отслеживаемых файлов; репозиторий — точный
  HEAD `1e2b27126e4500edc3ad40f733575f9122b9d7c4`, clean до и после. Candidate
  и snapshot отсутствовали до и после, записей нет. На момент dry-run
  следующим gate было отдельное разрешение exact `prepare --apply`.
  Apply-evidence от 2026-07-29: модель `gpt-5.6-sol`, sandbox
  `danger-full-access`, exact user-approved scope; запуск
  `20260729T102235Z-hermes-prepare-apply`. Ровно один обычный
  `prepare --apply` завершён с `exit 0`; JSON:
  `command=prepare`, `mode=apply`, `result=prepared`. Candidate
  `/home/openclaw/.hermes/mcp-rollout-state/hermes-kanban-mcp-30500cf973a40bb0918d33eb0476c1025e08ac0f`
  зарегистрирован как detached worktree на exact HEAD
  `30500cf973a40bb0918d33eb0476c1025e08ac0f`, tracked-clean;
  `venv/bin/python` исполняемый, Python `3.12.3`. rollout-снимок
  `/home/openclaw/.hermes/mcp-rollout-state/snapshots/6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`
  имеет каталог `0700`, ровно `manifest.json`, `wrapper.before`,
  `wrapper.after` mode `0600`, `schema_version=2`,
  `snapshot_kind=rollout`, `runtime_path_replacements=1`; SHA-256:
  manifest `83db7f0c4cd2a3239e5d52402f6b8b88e1a66ca46ba1daa5677249fcac4a196f`,
  before `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`,
  after `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`.
  Stable wrapper независимо подтверждён неизменным: regular non-symlink
  `0755`, `286` bytes, SHA-256 `17052c7d...`, ссылки `baseline=1`,
  `candidate=0`. Baseline — точный HEAD `6f8738dc...`, без изменений; репозиторий —
  точный HEAD `347c74323cbd4fd56a36be972be4c8142a7dca66`, без изменений.
  Switch/restart/deploy/process smoke/DB/MCP/Kanban действий не было,
  secrets не читались. Следующий gate — ordinary `switch` dry-run без
  `--apply`.
- [ ] 15.6 Выполнить обычный target `switch` dry-run и gated apply; не менять
  глобальный Hermes symlink, connector config, DB или services.
- [ ] 15.7 Только после repository lifecycle отдельно заменить standalone
  Kanban MCP process и выполнить bounded initialize/tools-list/dry-run sync
  smoke без DB writes.
- [ ] 15.8 При любом провале использовать соответствующий schema v2 rollback
  сначала dry-run, затем gated apply; восстановить wrapper byte-identically,
  не удаляя baseline/target/snapshots.
- [ ] 15.9 Сохранить exact plan/manifest/hash/process/smoke или rollback
  evidence. Ни merge PR, ни planning approval не считаются live approval.
