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
  считается разрешением на live-действия.
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
  полным filesystem before/after oracle и отсутствием вызовов примитивов записи.
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
- [x] 10.5 Запустить точечные tests только через
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
  обычные rollout snapshots на exact schema v2 без совместимости schema v1.
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
  запретом примитивов записи; state root до apply отсутствует.
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
  с symlink parent и broad target до примитивов записи. Production source не
  потребовал изменений; focused suite — `73 passed`, `py_compile` и
  `git diff --check` зелёные, максимальный размер файла — `978` строк.

## 14. Review и отдельный bootstrap-helper PR

- [x] 14.1 Проверить exact область diff: два helper source files, два helper
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
- [x] 15.6 Выполнить обычный target `switch` dry-run и gated apply; не менять
  глобальный Hermes symlink, connector config, DB или services.
  Частичная evidence от 2026-07-29: первый запуск
  `20260729T122316Z-hermes-switch-dryrun` fail-closed остановился до helper
  из-за DNS GitHub внутри read-only sandbox; изменений и запуска helper не
  было. Внешний read-only `git ls-remote` на HOSTKEY подтвердил remote main
  `55b436ac284de514263e94311961d7e58f236a59`. Успешный offline-запуск
  `20260729T122702Z-hermes-switch-dryrun-offline`, модель `gpt-5.6-sol`,
  sandbox read-only: выполнен ровно один actual `switch` dry-run без
  `--apply`, `exit 0`; JSON: `command=switch`, `mode=dry-run`,
  `snapshot_kind=rollout`, snapshot
  `6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`.
  `wrapper.before=17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`,
  planned
  `wrapper.after=5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`.
  Контрольные значения до и после совпали: корень состояния
  `798f493e4bc2522f5963a8137333cf2d1a00a6b6f930a813bf03fdf431f7187a`,
  snapshot
  `f008bac0e75a1e4bc294dac6a1ae1c63494d29e5c1c9d06d0ac610e228e8f31e`,
  контрольное значение wrapper
  `d3a3620c44d267f08d90d65af1ff07fb9480cd2aa65305f3e04ed9691af90625`,
  байты wrapper
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`,
  worktrees
  `0de76d66263f0cb981bb96f07042f0143c5abe6ab7884bc2404cf278bc7230ff`.
  State counts неизменны: `total=20660`, `files=17910`, `dirs=2743`,
  `symlinks=8`, `top_level=3`; временные rollout-файлы отсутствуют. Repo main
  clean на точном `55b436ac284de514263e94311961d7e58f236a59`; baseline
  `6f8738dc308f909bf1735883344f2fcc12f3cbcd` и candidate
  `30500cf973a40bb0918d33eb0476c1025e08ac0f` detached, clean и на точных SHA;
  snapshot и interpreter валидированы. Stable wrapper остался baseline;
  switch/restart/deploy/process smoke/DB/MCP/Kanban sync не выполнялись.
  Следующий точный gate — отдельное разрешение на `switch --apply` с expected
  исходный SHA wrapper
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`.
  Apply-evidence от 2026-07-29: запуск
  `20260729T124801Z-hermes-switch-apply`, модель `gpt-5.6-sol`, sandbox
  `danger-full-access`, точно одобренная пользователем область. Точная команда `switch --apply`
  выполнен ровно один раз: `exit 0`; JSON `command=switch`, `mode=apply`,
  `result=switched`. Stable wrapper атомарно заменён:
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9` →
  `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`;
  это regular non-symlink `0755`, `286` bytes, побайтово равный snapshot
  `wrapper.after`; ссылки baseline/candidate/export изменились `1/0/0` →
  `0/1/0`, контракт `mcp serve-kanban --allow-write` сохранён. Repo exact
  ветка main и локальный origin
  `47c3f9e8f159fbf9bd891429db7abab081331156`, без изменений; SHA-256 snapshot
  manifest
  `83db7f0c4cd2a3239e5d52402f6b8b88e1a66ca46ba1daa5677249fcac4a196f`.
  До и после совпали: snapshot oracle
  `de01265155da79290c444d76ac29c55411b3a0bbdb2b716ec5f2f41d2a3a018d`,
  контрольное значение корня состояния
  `0902084da8ab7d83ae476c6b634dfb52725d2d9fe23bfb1fa498c4107750fd5a`,
  контрольное значение baseline
  `9d2347c35662c9e9a9c9a9d0f94f46ba0a6eef2ec3da886fe541be71139e1e57`
  и контрольное значение candidate
  `b89a0fc05617e7131c8ad8443ffa43ea0a9bff7e663ec6b3abaa2d22f06ecbad`;
  rollout temp files отсутствуют. Process oracle до и после совпал: `14`
  существующих standalone Kanban MCP процессов не перезапускались и всё ещё
  используют baseline interpreter/command; это подтверждено независимой
  проверкой. Restart/deploy/process replacement/smoke, rollback,
  DB/MCP/Kanban calls и network не выполнялись. Следующий отдельный gate —
  `15.7`: отдельное разрешение заменить standalone Kanban MCP process и
  выполнить bounded initialize/tools-list/dry-run sync smoke без DB writes.
- [ ] 15.7 Только после repository lifecycle отдельно заменить standalone
  Kanban MCP process и выполнить bounded initialize/tools-list/dry-run sync
  smoke без DB writes.
  Evidence ownership/import-origin audit: unbiased process audit без VPS Codex
  обнаружил на HOSTKEY `11` live stdio MCP children; каждый является direct
  child отдельного `sshd: openclaw@notty`, а не systemd/standalone service,
  имеет pipes на `fd0/fd1/fd2` и отдельный session scope. Из них `7`
  используют baseline interpreter `6f8738dc...`, `4` — candidate interpreter
  `30500cf...`. На Windows им соответствуют те же `11` процессов `ssh.exe` с
  точная команда
  `hostkey-codex /home/openclaw/.hermes/mcp/hermes-kanban/run.sh`; все они —
  children `codex.exe` PID `26004`, поэтому массовое завершение затронуло бы
  другие задачи Codex. Read-only pulse `mode=ro/query_only`
  `kanban_board_status` по I/O точно связал текущий connector с HOSTKEY PID
  `3118916` на candidate runtime; по времени ему соответствует Windows
  `ssh.exe` PID `428`.
  Import-origin audit: stable wrapper SHA `5e03752f...` запускает
  `candidate/venv/bin/python -m hermes_cli.main`, однако fresh candidate
  interpreter разрешает импорты `hermes_cli.main` и
  `agent.transports.hermes_kanban_mcp_server` из
  `candidate/venv/lib/python3.12/site-packages`, дистрибутив `hermes-agent
  0.18.2`. Candidate source server имеет SHA-256
  `dd065b21caa73cda4c1f2d74a0139afed0c5df880ce65d9a27b0ed8b3dcc8e1f` и
  содержит `2` ссылки `kanban_sync_external_task`, тогда как server в
  candidate venv site-packages имеет SHA-256
  `fa01ac3d129f875144f40df0cc512a561ecbb8e018b0ba467f0f91c97376e174`,
  побайтово совпадает с baseline venv site-packages и не содержит
  `kanban_sync_external_task`. Runtime `WRITE_TOOLS` остаётся старым и не
  включает external sync. Поэтому process replacement/smoke не может
  подтвердить feature и запрещён до rollback/remediation.
- [x] 15.8 При любом провале использовать соответствующий schema v2 rollback
  сначала dry-run, затем gated apply; восстановить wrapper byte-identically,
  не удаляя baseline/target/snapshots.
  Partial evidence rollback dry-run: выполнена exact rollback-команда для
  snapshot `6f8738dc...-to-30500cf...` с expected current wrapper SHA
  `5e03752f...`, без `--apply`; `exit 0`, JSON `command=rollback`,
  `mode=dry-run`. Planned `wrapper.before` SHA —
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`.
  До и после stable wrapper SHA остался
  `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`,
  контрольное значение снимка —
  `9bb98617befe30c274a049cecd2ee68408b19aec7cfc3b7424a29ade3d7a6bf9`,
  process counts — `baseline:7`, `candidate:4`; временные файлы отсутствуют,
  `NO_WRITES=yes`. Rollback `--apply` не выполнялся. Следующий отдельный gate
  — явное разрешение на exact rollback `--apply`; после rollback требуется
  material baseline исправления для согласованности source/package.
  Apply evidence предоставлен пользователем для этого material delta:
  rollback wrapper успешно применён для snapshot
  `6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`,
  `exit 0`; stable wrapper восстановлен до SHA-256
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`.
  Restart, process replacement, DB, Kanban operations и smoke не выполнялись.
- [ ] 15.9 Сохранить exact plan/manifest/hash/process/smoke или rollback
  evidence. Ни merge PR, ни planning approval не считаются разрешением на live-действия.

## 16. Material repair baseline для runtime coherence

- [x] 16.1 Зафиксировать root cause: copied candidate venv импортировал
  `hermes_cli.main` и `agent.transports.hermes_kanban_mcp_server` из old
  `site-packages`, тогда как source checkout target содержит новый
  `kanban_sync_external_task`.
- [x] 16.2 Обновить на русском proposal, design, capability spec и tasks:
  schema v3 для новых rollout snapshots, `source-cwd-v1` wrapper,
  sanitized no-DB import-origin preflight, совместимость v2 read/rollback,
  независимость rollback от imports candidate и новые гейты доставки.
- [x] 16.3 Получить отдельное явное одобрение material repair baseline до
  implementation; planning approval не является разрешением на live-действия.
  Baseline явно одобрен пользователем 2026-07-29; live scope не расширен.
- [x] 16.4 Реализовать отдельным task-owned PR без live actions:
  детерминированный canonical `wrapper.after`, schema v3 manifest/evidence,
  import-origin preflight до snapshot и повторные `switch` guards; не
  использовать network, `pip`, editable install или `.pth`.
  Выполненная попытка сохранена как history, но independent review вынес
  `BLOCK`; implementation помечена needing remediation по разделу 18.
- [x] 16.5 Сохранить legacy schema v2 wrapper как допустимый `before` и
  rollback target; v2 rollback восстанавливает exact bytes/mode и не зависит
  от исправности imports candidate.
  Выполненная попытка не закрывает новый snapshot-only rollback contract:
  review доказал зависимость общего loader от source/candidate runtime.
- [x] 16.6 Добавить точечные тесты только во временном окружении: rollout из
  legacy в canonical, rollout из canonical в canonical, совместимость v2
  rollback, v3 switch/rollback и отказ на malformed/ambiguous wrapper,
  затенение старым `site-packages`, dry-run без записи и точный список tools.
  Выполненная test matrix сохранена, но review выявил validation gaps; нужные
  дополнительные tests перечислены и выполнены в 18.6.
- [x] 16.7 Проверить source/test files `<1000`, focused suite через
  `scripts/run_tests.sh`, strict OpenSpec validation, `git diff --check` и
  independent review без `BLOCK`.
  Выполненное evidence 2026-07-29: focused suite из четырёх affected файлов
  через `scripts/run_tests.sh` — `100 passed`, `0 failed`; максимальный
  source/test file — `992` строки; `git diff --check` и
  `openspec validate --strict --no-interactive` зелёные. Task остаётся
  открытым. Independent review
  `20260729T144156Z-kanban-runtime-coherence-review` вынес verdict `BLOCK`;
  state module на 992 строках не имеет заметного запаса. Нужна remediation
  18.1–18.7 и новый review без `BLOCK`. Последующий run
  `20260729T192126Z-kanban-os-sandbox-independent-review` также вынес
  `BLOCK`; историческая author-local remediation 20.2–20.6 и два
  последовательных four-suite runs выполнены. После следующего `BLOCK`
  completion claim 20.2 отозван; задача остаётся открытой до новой
  реализации, independent validation и accepted review.
  Run `20260729T224514Z-kanban-remediation-independent-review` выполнил exact
  four-suite command два раза успешно, но снова вынес `BLOCK`: nested
  in-place mutation, FD cleanup и test-harness acceptance не закрыты.
  Зелёные runs являются evidence, не acceptance; task остаётся открытым.

## 17. Post-merge live gates после repair PR

- [ ] 17.1 После merge repair PR отдельно запросить `prepare` dry-run и
  сохранить exact plan/hash evidence; без `--apply`. Этот dry-run не создаёт
  candidate и не должен обещать import-origin evidence.
- [ ] 17.2 Только после отдельного approval выполнить `prepare --apply`;
  впервые получить и проверить import-origin evidence, schema v3 snapshot,
  canonical `wrapper.after` и отсутствие process/DB/Kanban actions.
- [ ] 17.3 Отдельно запросить `switch` dry-run; повторно доказать exact
  проверки wrapper/hash/runtime/import-origin.
- [ ] 17.4 Только после отдельного approval выполнить `switch --apply`; не
  менять global Hermes symlink, connector config, DB или services.
- [ ] 17.5 После repository lifecycle отдельно заменить current connector и
  выполнить bounded `initialize`/`tools-list`/`kanban_sync_external_task`
  dry-run smoke без DB writes.

Все tasks 17.x заблокированы до повторного одобрения и выполнения material
OS-sandbox delta 19.1–19.9, закрытия 16.7/18.8 accepted independent review,
PR/merge и отдельных live approvals.

## 18. Material remediation delta после independent review `BLOCK`

- [x] 18.1 Получить отдельное явное одобрение этого material remediation
  delta до implementation. Approval является только planning gate и не
  разрешает implementation задним числом, live/runtime/process/DB actions,
  commit, push или PR.
  Material remediation baseline явно одобрен вызовом `@best-step`
  2026-07-29; live scope не расширен.
- [x] 18.2 Вынести exact wrapper grammar и isolated import-origin policy в
  `scripts/hermes_kanban_mcp_runtime_coherence.py`; оставить
  `hermes_kanban_mcp_rollout_state.py` только schema/snapshot/transition и
  обеспечить заметный запас ниже 1000 строк, не `999`.
  Реализовано: coherence module имеет 544 строки, state module — 768 строк,
  то есть запас state до лимита составляет 232 строки.
- [x] 18.3 Реализовать candidate preflight exact interpreter с `-I -S -B`,
  exact allowlisted environment, synthetic `HOME`/`HERMES_HOME`, без
  `PYTHONPATH`/`PYTHONHOME` и исполнения `.pth`; использовать `find_spec` для
  `hermes_cli.main`, затем guarded import dedicated server. До import
  fail-closed запретить file writes, network/socket, subprocess/`os.system` и
  DB opens; не отражать arbitrary stderr.
  Реализовано и проверено faithful temp fixture: `hermes_cli.main` не
  импортируется, `.pth` не исполняется, exact env/argv зафиксированы, все
  side-effect traps закрываются безопасной ошибкой без отражения stderr.
- [x] 18.4 Разделить полную switch/runtime-coherence validation и отдельный
  snapshot-only rollback loader. Rollback schema v2/v3 должен сохранять exact
  snapshot/hash/current-wrapper/`wrapper.before` guards, но не требовать
  source repo, candidate runtime/venv/imports и работать при отсутствующем,
  повреждённом или грязном candidate.
  Реализовано двумя loaders; v2 и v3 rollback проверены при unavailable
  source repo и missing/corrupt/dirty candidate без runtime/preflight calls.
- [x] 18.5 Заменить token-presence wrapper parser на exact allow-listed
  legacy/canonical templates: shebang, `set`, exports, единственный `exec` с
  exact argv; canonical `cd --` непосредственно перед `exec`. Отклонять
  comments-only, missing `exec`, extra commands, redirects и shell control
  operators.
  Реализована deterministic grammar с сохранением allow-listed header при
  legacy→canonical и canonical→canonical generation.
- [x] 18.6 Расширить три temp-only helper test files: реальные target modules
  либо точный fixture, synthetic HOME и oracle вне root, ловушки побочных
  эффектов; rollback при отсутствующем/повреждённом/грязном candidate,
  изоляция реального HOME, запрет исполнения `.pth`, network/file/process/DB,
  очистка stderr, комментарии вместо команды, отсутствие `exec`, лишние
  команды, перенаправления и операторы shell.
  Три helper files дали `102 passed`, отдельный exact-list adapter file —
  `22 passed`; все artifacts и side-effect oracle находятся во временных
  каталогах.
- [x] 18.7 Проверить contract evidence: `prepare` dry-run сообщает только
  plan/hashes; `prepare --apply` впервые сообщает origin; `switch` dry-run и
  apply повторяют origin audit. Запустить focused suite через
  `scripts/run_tests.sh`, line-count gate с заметным запасом state module,
  strict OpenSpec validation и `git diff --check`.
  Evidence 2026-07-29: единый affected run четырёх files через
  `scripts/run_tests.sh` — `124 passed`, `0 failed` (`42+31+29+22`);
  количество строк исходников/тестов — `633/768/544/987/661/648`;
  проверки свидетельств `prepare`/`switch` зелёные; strict OpenSpec и
  `git diff --check`
  успешны. Task 18.8 и 16.7 остаются открытыми до нового independent review
  без `BLOCK`.
  Second remediation evidence 2026-07-29 после review
  `20260729T154055Z-kanban-runtime-coherence-remediation-rev`: violation
  сделан sticky после подавленного исключения; audit/monkeypatch policy
  закрывает filesystem mutations, process/kill/exec, socket/network, DB и
  `ctypes`/`cffi` FFI paths. До preflight закрепляются regular non-symlink
  `pyvenv.cfg`, его SHA-256, exact contained non-symlink `site-packages` и
  trusted stdlib roots; symlink-resolved external package отклоняется.
  Wrapper использует exact bash/`set -euo pipefail` template, обязательные
  ordered exports с literal absolute `HERMES_HOME` и
  `PYTHONDONTWRITEBYTECODE=1`, optional exact `cd` и единственный exact
  `exec`. Schema-v2 test fixture и expected wrapper bytes независимы от
  production schema set/generator. Финальный affected run четырёх files
  через `scripts/run_tests.sh` — `143 passed`, `0 failed`
  (`31+42+48+22`); количество строк —
  `649/812/718/996/680/816/765`, strict OpenSpec и `git diff --check`
  успешны. README обновлён под schema v3 и три helper suites. Task 18.8 и
  16.7 намеренно остаются открытыми до нового independent review без
  `BLOCK`.
- [x] 18.8 Получить новый independent review без `BLOCK`; только после этого
  закрыть 16.7 и продолжить PR/merge lifecycle. Author-local remediation
  завершена, но delivery остаётся заблокированным до independent review.
  Третье review
  `20260729T161437Z-kanban-runtime-coherence-final-review` снова вынесло
  `BLOCK`: Python-level policy обходится через low-level/native paths,
  candidate startup предшествует trust boundary, schema-v2 fixture не
  является историческим golden, rollout test вырос до 996 строк. Task
  остаётся открытым; раздел 19 теперь утверждён для implementation и
  repo-local/temp-only verification, но не для review closure, delivery или
  live-действий.
  Четвёртый run
  `20260729T192126Z-kanban-os-sandbox-independent-review` снова вынес
  `BLOCK`: descriptor trust не связан с exact objects, переданными `bwrap`,
  provenance содержит raw host paths, extraction оставила façade/26 строк
  запаса, четыре regressions потеряны, а независимые tests не собраны.
  Material baseline из 20.x был одобрен и реализован author-local, но
  completion claim 20.2 позднее отозван; task остаётся открытым.
  Пятый run
  `20260729T224514Z-kanban-remediation-independent-review` дал два зелёных
  exact four-suite runs, но verdict `BLOCK`: directory descriptors не
  защищают nested regular-file bytes от in-place mutation, resource ownership
  и измеримая test extraction не закрыты. Task остаётся открытым; раздел
  21.x требует нового approval.

## 19. Утверждённый material OS-sandbox delta после третьего `BLOCK`

- [x] 19.1 Получить повторное явное одобрение proposal/spec/design/tasks этого
  material delta. До approval не менять implementation или tests и не
  выполнять commit, push, PR либо live-действия.
  Явное одобрение получено от пользователя 2026-07-29 формулировкой:
  «material OS-sandbox delta без live rollout». Одобрение разрешает только
  реализацию и repo-local/temp-only проверки 19.2–19.8 в task-owned worktree;
  коммит, отправка изменений, `PR`, изменение обёртки в `live`/`staging`,
  перезапуск, замена процесса, дымовая проверка `MCP`, `DB` и развёртывание
  остаются запрещены.
- [x] 19.2 До любого candidate Python `exec` установить основной boundary
  через exact `/usr/bin/bwrap`: fail-closed capability probe без Python-only
  fallback, пустой mount namespace, read-only candidate/runtime и только
  необходимые `/usr`/`/lib*`, раздельные tmpfs HOME/HERMES_HOME/temp,
  `--clearenv` с exact allowlist, свежий `/proc`, минимальный `/dev`,
  user/PID/IPC/UTS/cgroup/network namespaces насколько поддержано,
  `--new-session`, `--die-with-parent`, без host sockets, новых daemon, root,
  deploy, `nsjail` или `systemd-run`.
  Выполнено: `scripts/hermes_kanban_mcp_os_sandbox.py` закрепляет exact
  regular executable `/usr/bin/bwrap` открытым descriptor и SHA-256,
  отдельный probe проверяет baseline executable/namespaces, а candidate
  запускается только полным production invocation с read-only required
  content/data binds, тремя tmpfs и exact clearenv allowlist. Только
  production invocation является authoritative проверкой полного профиля;
  его ошибка, а также missing/symlink/broken `bwrap` завершаются fail-closed,
  fallback отсутствует. Предыдущее утверждение о полном production profile в
  baseline probe отозвано.
- [x] 19.3 Сформулировать и реализовать security contract как отсутствие
  host-visible side effects: bubblewrap namespaces являются boundary, а
  Python audit/sticky denial/monkeypatch остаются вторым слоем и evidence, не
  доказательством запрета каждого внутреннего syscall. Seccomp не делать
  обязательной зависимостью; рассматривать только как future hardening при
  доказанном тестами остаточном риске.
  Выполнено: mount/PID/user/IPC/UTS/cgroup/network isolation является
  основным boundary для host-visible effects; встроенный audit hook, sticky
  denial и monkeypatch применяются только как второй слой и структурированная
  диагностика. Контракт не утверждает, что каждый syscall внутри sandbox
  обязан завершиться `EPERM`.
- [x] 19.4 Заменить недостаточный directory-descriptor trust на sealed
  content bundle по 21.2: descriptor-relative/O_NOFOLLOW manifest,
  материализация каждого executable/importable regular file, anchors/digests
  из тех же sealed captured bytes и отсутствие bind mutable backing
  directory.
  Историческое evidence сохранено: parent bundle действительно удерживает
  directory/interpreter descriptors через child/post-check/switch. Completion
  claim отозван после
  `20260729T224514Z-kanban-remediation-independent-review`: directory FD не
  замораживает nested regular-file bytes и не закрывает in-place mutation.
  Выполнено по approved baseline 2026-07-30: production invocation строится
  только из sealed regular-file bytes и manifest topology, без
  bind-монтирования изменяемых каталогов-источников.
- [x] 19.5 Добавить реальный статический sanitized schema-v2 golden из
  исторического snapshot и wrapper с provenance, исходными SHA-256,
  SHA-256 sanitized blobs и исчерпывающим ordered списком sanitization
  substitutions. Ни один из четырёх fixture files не содержит raw
  `/home/openclaw`; каждая substitution содержит `file/field`, source class,
  source hash, literal replacement, count и reason без raw source value.
  Payload bytes/hashes `manifest.json`, `wrapper.before`, `wrapper.after` и
  snapshot-only semantics сохранить неизменными.
  Remediation выполнена: raw prefix отсутствует во всех четырёх files;
  `provenance.json` хранит ordered ledger с exact `file`/`field`,
  `source_class`/`source_sha256`, literal `replacement`, `count` и `reason`
  без source literal. Payload bytes и SHA-256 не изменились.
- [x] 19.6 Реально разгрузить
  `tests/scripts/test_hermes_kanban_mcp_rollout.py`: вынести общий
  Git/layout/oracle harness в существующий
  `hermes_kanban_mcp_test_support.py` как содержательный reusable owner без
  thin forwarding; сохранить behavior и regressions. Gates: rollout
  `<=850`, support `<400`, каждый source/test `<1000`,
  `runtime_coherence.py <=900`.
  Историческое evidence сохранено: common production primitives имеют
  единственного owner, state façade удалён, четыре regressions возвращены,
  `runtime_coherence.py` имеет 899 строк. Completion claim отозван: rollout
  test имеет 999 строк, support — 40 и не владеет содержательным harness.
  Выполнено: rollout test — 770 строк, support owner — 295,
  runtime coherence — 832; каждый затронутый source/test меньше 1000 строк.
- [x] 19.7 Расширить temp-only acceptance/bypass matrix: direct
  `subprocess._fork_exec`, `ctypes`/native write/network, signal и
  `resource.prlimit`, подмены интерпретатора/`pyvenv.cfg`/исходного
  кода/`venv` через символьные ссылки/TOCTOU, поддельные свидетельства,
  nested in-place mutate→candidate import/effect→restore после sealed capture
  с fully matching forged child evidence, missing/broken `bwrap` и неизменные
  host canaries. Аналогично покрыть trusted stdlib regular file и, где
  практично, interpreter/`bwrap` bytes. Sandbox обязан выполнить только
  sealed original bytes либо fail-closed; host side-effect отсутствует.
  Отдельно сохранить snapshot-only rollback contract и различие между базовой
  пробой и полным вызовом.
  Историческое swap-and-restore/path-replacement evidence сохранено, но
  completion claim отозван: оно не выполняло nested in-place mutation
  regular-file bytes и поэтому не закрывает новый acceptance.
  Выполнено: temp-only matrix покрывает candidate/stdlib/interpreter
  in-place mutation после capture, sealed bwrap oracle,
  acquisition/capture/handoff failures и отсутствие FD leaks.
- [x] 19.8 После implementation запустить четыре helper test modules только
  через `scripts/run_tests.sh`, доступную repo-local русскую проверку, strict
  OpenSpec validation, `git diff --check` и line-count/scope gates; получить
  accepted independent review без `BLOCK`. Только после accepted review
  разрешены commit, push и task-owned PR.
  Авторское historical evidence `124 passed` сохранено, но независимо не
  подтверждено. Review run
  `20260729T192126Z-kanban-os-sandbox-independent-review` дважды завершил
  exact four-suite команду до collection: `FileNotFoundError` usable temp и
  `EROFS` для `test_durations.json`; `0 passed`, `0 failed`. Task остаётся
  открытым: author-local remediation дала два последовательных run по
  `132 passed`, но ещё требуются workspace-write/source-read-only
  independent validation и accepted review; 18.8, 16.7 и 19.9 также
  остаются открытыми.
  Независимый запуск
  `20260729T224514Z-kanban-remediation-independent-review` выполнил точную
  команду для четырёх наборов тестов два раза подряд успешно, но вердикт
  остался `BLOCK`.
  Оба зелёных запуска записаны как evidence, не acceptance: после них
  переоткрыты 19.4/19.6/19.7 и добавлен material раздел 21.x. Reviewer-only
  source mutation была побайтово восстановлена, fingerprints совпали, но
  probe исключён из mandatory evidence. Task остаётся открытым до новой
  реализации по approval и повторной source-read-only independent validation.
- [ ] 19.9 После accepted review пройти обычный PR/merge lifecycle. Live
  rollout, wrapper replacement, restart, process replacement и DB остаются
  запрещены до отдельного exact разрешения; planning approval, tests, review,
  commit, push, PR или merge не открывают этот gate.

## 20. Material remediation после independent run `20260729T192126Z`

- [x] 20.1 Получить новое явное approval этого material remediation baseline
  до любых изменений implementation, tests или fixtures. Предыдущее approval
  OS-sandbox delta не переносится на 20.2–20.7. Approval MAY разрешить только
  repo-local implementation/temp-only verification; commit, push, PR и
  live-действия остаются закрытыми.
  Явное approval получено от пользователя 2026-07-29 для repo-local
  implementation/temp-only verification; commit, push, PR и live scope не
  разрешены.
- [x] 20.2 Реализовать единый sealed parent trust bundle по новому контракту
  21.2–21.3: captured regular-file bytes, manifest topology и exception-safe
  FD ownership вместо directory-descriptor selection. Добавить nested
  in-place mutation acceptance с fully matching forged child evidence.
  Историческое evidence сохранено: descriptor-bound invocation не
  переоткрывала managed directory paths, FDs жили через switch replacement и
  закрывались после post-check. Completion claim отозван: nested file
  content оставался mutable, а write/lseek/seal failure paths не доказывали
  полный cleanup.
  Выполнено по 21.2–21.5: единый owner удерживает manifest и sealed FDs через
  comparison/post-check/switch replacement и возвращает structured cleanup
  evidence при ошибке.
- [x] 20.3 Выделить настоящий common ownership module для общих
  path/Git/venv primitives; перевести consumers на прямые imports, удалить
  forwarding re-export façade из state boundary. Проверить
  `runtime_coherence.py <=900`, все остальные source/test files `<1000` и
  отсутствие дублирующих common primitives; exact имя/внутренняя раскладка
  common module не являются planning contract.
  Выполнено: `hermes_kanban_mcp_rollout_common.py` является единственным
  owner 16 общих primitives; forwarding assignments из state удалены.
  Количество строк: исходники (`source`) `678/895/258/899/330`, тесты (`tests`)
  `688/999/779/589`, support `40`.
- [x] 20.4 Полностью санитизировать historical fixture bundle: raw
  `/home/openclaw` отсутствует во всех четырёх files, включая
  `provenance.json`; каждая substitution ledger entry содержит `file/field`,
  source class, source hash, literal replacement, count и reason без raw
  source value. Сохранить exact bytes/hashes трёх payload files и
  семантику отката только по снимку.
  Выполнено: все четыре fixture files независимо проверяются literal oracle;
  payload SHA-256 сохранены
  `73c1ff3f...`, `95e89250...`, `f5ed7ba0...`, SHA-256 происхождения (`provenance`) —
  `0bbd0898...`.
- [x] 20.5 Вернуть в focused suite четыре удалённые regression: existing
  candidate, existing snapshot, symlink stable wrapper и future
  candidate/snapshot parent symlink. Они должны выполняться как behavioral
  tests, а не source-text checks.
  Выполнено behavioral tests без source-text inspection: existing candidate,
  existing snapshot, symlink stable wrapper и parametrized future
  candidate/snapshot parent symlink завершаются без новых side effects.
- [x] 20.6 Закрепить честный capability contract: отдельный probe является
  только baseline executable/namespaces/mounts probe; реальный production
  invocation со всеми required content/data binds и exact candidate argv является
  единственным доказательством полного профиля и при любой ошибке закрывается
  fail-closed без fallback.
  Выполнено: `_probe` проверяет только baseline; отдельный test принудительно
  отклоняет полный descriptor-bound invocation после успешного probe и
  подтверждает fail-closed без candidate fallback.
- [x] 20.7 Выполнить независимую validation в `workspace-write` sandbox при
  source-read-only review policy. Tests могут писать только
  temp/cache/evidence; pre/post source diff обязан совпасть. Одну exact
  four-suite команду через `scripts/run_tests.sh` запустить успешно два раза
  подряд; run без collection, environment blocker или один успешный run не
  закрывает task. Затем выполнить strict OpenSpec, `git diff --check`,
  гейты количества строк, владения и области изменений.
  Авторское repo-local evidence: exact four-suite команда два раза подряд
  завершилась `132 passed, 0 failed` (`31+43+28+30`); `git diff --check`,
  line/owner/fixture/scope gates зелёные. Task остаётся открытым, потому что
  независимая source-read-only validation ещё не выполнялась.
  Независимые свидетельства:
  `20260729T224514Z-kanban-remediation-independent-review`: точная команда для
  четырёх наборов тестов дважды завершилась успешно. Это не закрывает task,
  потому что
  review verdict `BLOCK`, material acceptance изменён, а reviewer-only
  временная source mutation, хотя и byte-restored с совпавшими
  fingerprints, исключена из mandatory evidence. Нужна повторная validation
  после одобренной реализации 21.x.
- [x] 20.8 Получить accepted independent review без `BLOCK`. Только после
  этого MAY закрываться 16.7, 18.8 и 19.8 и начинаться 19.9; выполненные
  tasks 19.4, 19.6, 19.7 и 20.2 также должны быть заново закрыты по 21.x;
  сейчас они открыты. Independent run
  `20260729T224514Z-kanban-remediation-independent-review` завершился
  `BLOCK`, несмотря на два зелёных four-suite runs. Live
  rollout/restart/process replacement/MCP/DB/systemd/deploy/network остаются
  запрещены без отдельного exact разрешения независимо от результата review.

## 21. Material sealed-content delta после independent run `20260729T224514Z`

- [x] 21.1 Получить новое явное approval proposal/spec/design/tasks этого
  material delta. Предыдущие approvals 19.x/20.x не переносятся. До approval
  запрещены implementation, изменения scripts/tests/fixtures и
  implementation test runs. Commit, push, PR и любые live/staging/process/
  MCP/Hermes/Gurra/systemd/DB/deploy/network действия остаются закрытыми.
  Явное approval sealed-content baseline получено от Руслана 2026-07-30.
- [x] 21.2 Реализовать sealed content bundle. Descriptor-relative с
  `O_NOFOLLOW` построить полный манифест топологии каталогов, символических
  ссылок и обычных файлов; каждый исполняемый или импортируемый обычный файл
  из дерева
  исходного кода кандидата, точный интерпретатор, `pyvenv.cfg`, необходимую
  доверенную стандартную библиотеку, замыкание загрузчика и разделяемых
  библиотек, доверенный тестовый каркас и байты `bwrap`
  материализовать в sealed immutable memfd/data binding. Anchors/digests
  строить из этих же captured bytes. `bwrap` передавать только sealed bundle
  и созданную из manifest topology, без bind mutable backing directory.
  Incomplete/changed manifest, unsupported type, escape или невозможность
  sealed execution закрывать fail-closed. Контракт начинается после
  успешного capture и гарантирует exact captured verified bytes до
  `exec`/import, не исторические bytes до capture.
  Выполнено: candidate/venv/stdlib/interpreter/bwrap/ELF closure и trusted
  data захватываются в sealed memfd; topology передаётся только через
  монтирования файлов данных, каталогов и символьных ссылок.
- [x] 21.3 Сделать resource ownership exception-safe. Каждый успешный
  `open`/`memfd_create` регистрировать немедленно; `_data_fd` обязан закрыть
  current FD при write/lseek/readback/hash/seal failure до handoff. Partial
  bundle cleanup закрывает все ранее приобретённые FDs; cleanup error
  возвращается как structured fail-closed error вместе с primary failure и
  `replacement_applied` state, не скрывается и не разрешает continuation.
  Выполнено: immediate ownership, current-FD cleanup, partial cleanup retry и
  structured primary/cleanup/replacement state проверены failure injection.
- [x] 21.4 Вынести общий Git/layout/oracle harness в существующий
  `tests/scripts/hermes_kanban_mcp_test_support.py` как содержательного
  reusable owner без thin forwarding. Сохранить behavior и все regressions.
  Измеримые gates: `test_hermes_kanban_mcp_rollout.py <=850` строк, support
  `<400`, каждый source/test `<1000`, `runtime_coherence.py <=900`.
  Выполнено: support owner — 295 строк, rollout test — 770, runtime coherence
  — 832; остальные затронутые source/test также меньше 1000.
- [x] 21.5 Добавить temp-only adversarial/failure-injection tests. Обязателен
  nested in-place mutate→candidate import/effect→restore после sealed capture
  с fully matching forged child evidence: выполняются только sealed original
  bytes либо fail-closed, host side-effect отсутствует. Аналогично покрыть
  trusted stdlib regular file и, где практично, interpreter/`bwrap` bytes.
  На каждой acquisition/capture/handoff стадии injected failure должен
  доказать закрытие current и всех ранее зарегистрированных FDs, отсутствие
  leaked FDs и видимый structured cleanup error. Сохранить snapshot-only
  rollback, host-canary и четыре path/security regressions.
  Выполнено: targeted acceptance — `14 passed`; exact four-suite два раза
  подряд — `140 passed, 0 failed` (`31+43+28+38`) без FLAKY.
- [x] 21.6 После implementation выполнить author validation: одну exact
  four-suite команду два раза подряд:

  ```bash
  scripts/run_tests.sh \
    tests/scripts/test_hermes_kanban_mcp_bootstrap.py \
    tests/scripts/test_hermes_kanban_mcp_rollout.py \
    tests/scripts/test_hermes_kanban_mcp_runtime_coherence.py \
    tests/scripts/test_hermes_kanban_mcp_runtime_sandbox.py
  ```

  Затем выполнить strict OpenSpec, `git diff --check`, FD leak, line-count,
  ownership и exact scope gates. Если support extraction не добавляет test
  module, команда остаётся exact four-module. Зелёные author runs являются
  evidence и сами по себе не открывают review/delivery gate.
  Historical author evidence до нового thermo `BLOCK`: два
  последовательных exact run завершились `140 passed, 0 failed`
  (`31+43+28+38`). Task переоткрыт и не закрывается этим evidence, поскольку
  требования materially изменены разделом 22.x.
- [x] 21.7 Получить новую independent validation/review. Sandbox —
  `workspace-write` только для temp/cache/evidence; source, tests, fixtures и
  OpenSpec остаются read-only, pre/post source fingerprints совпадают. Exact
  four-suite command выполняется два раза подряд успешно; `0 collected`,
  environment blocker, один зелёный run или reviewer source mutation не
  закрывают acceptance. Reviewer-only deviation предыдущего run
  зафиксирован как byte-restored с совпавшими fingerprints и исключён из
  mandatory evidence. Требуется accepted verdict без `BLOCK`.
- [x] 21.8 Только после accepted independent review MAY закрыться 16.7,
  18.8, 19.8, 20.7 и 20.8 и начаться 19.9. Implementation tasks 19.4,
  19.6, 19.7 и 20.2 закрыты author evidence по approved 21.2–21.5, но это
  не является independent acceptance или delivery gate.
  Commit/push/task-owned PR остаются запрещены до этого gate. Live rollout,
  замена `wrapper`, перезапуск и замена процесса,
  MCP/Hermes/Gurra/systemd/DB/deploy/network остаются запрещены после
  review/PR/merge до отдельных exact
  approvals.

## 22. MATERIAL REMEDIATION DELTA после нового independent thermo `BLOCK`

- [x] 22.1 Принять finding нового independent thermo review и обновить
  truth state: verdict `BLOCK`; два author exact four-suite run по
  `140 passed, 0 failed` сохранить как historical evidence, не acceptance.
  Переоткрыть 19.4, 19.6, 19.7, 20.2, 21.2 и 21.5; сохранить открытыми
  21.6–21.8, delivery и live gates.
- [x] 22.2 Получить повторное явное approval этого proposal/spec/design/tasks
  MATERIAL REMEDIATION DELTA. До approval запрещены implementation,
  scripts/tests/fixtures changes и implementation test runs. Commit, push,
  PR, live rollout, restart, process/MCP/DB/deploy/network остаются
  запрещены.
  Exact approval получено от Руслана 2026-07-30 формулировкой:
  «одобряю material ELF/resource remediation baseline». Оно разрешает только
  implementation 22.3–22.6 и repo-local/temp-only author verification;
  commit, push, PR, independent/delivery и live scope не открыты.
- [x] 22.3 Реализовать двухфазный bounded inventory → sealed acquisition.
  Первый descriptor-relative `O_NOFOLLOW` проход удерживает только bounded
  малое число временных FD и строит topology, identities/digests и exact ELF
  plan без content memfd. После успешного resource plan второй проход
  захватывает sealed bytes и повторно сверяет topology/identity/digest;
  изменение или unsupported case завершается fail-closed со structured
  cleanup до invocation.
  Выполнено: отдельный bounded descriptor-relative inventory строит
  topology/identity/digest и ELF dependency plan без content memfd; resource
  plan предшествует второму acquisition, который повторно сверяет
  topology/identity/digest и закрывает partial owner при расхождении.
- [x] 22.4 Реализовать exact ELF closure: раздельные `DT_RPATH` и
  `DT_RUNPATH`, порядок приоритетов и наследование GNU/Linux, детерминированно
  раскрывать `$ORIGIN`, `$LIB`, `$PLATFORM` либо отклонять до capture. Relative/empty/unsafe/path
  escape отклонять. Dynamic segment сделать bounded и кратным entry size,
  требовать bounded `DT_NULL`, string offsets/terminators; `DT_NEEDED`
  принимать только как safe soname без slash, `NUL` и escape.
  Выполнено в отдельном ELF owner: parser хранит `RPATH`/`RUNPATH`
  раздельно, resolver моделирует direct RUNPATH и inherited legacy RPATH,
  tokens раскрываются только из exact platform facts, malformed/unsafe
  metadata отклоняется до acquisition без silent fallback.
- [x] 22.5 Реализовать детерминированный планировщик ресурсов до первого
  memfd содержимого и до invocation. Учесть уже открытые FD и элементы содержимого,
  FD для манифеста, загрузчика, `bwrap`, библиотек, тестового стенда, опорных
  данных, пробного и производственного наборов аргументов, явный резерв для
  дочернего процесса/`bwrap`, конечный `RLIMIT_NOFILE`, платформу, `pass_fds`
  и ограничения `bwrap`. Проверить exec `argv` + окружение против
  `SC_ARG_MAX` с именованным запасом и отдельный явный максимум размера
  сериализованной полезной нагрузки `bwrap --args`.
  Выполнено в отдельном resource owner: current/open/planned/fixed/reserve
  FDs проверяются против finite `RLIMIT_NOFILE`; topology, actual
  argv/environment и probe/production serialized args имеют отдельные
  именованные limits до соответствующих memfd/invocation.
- [x] 22.6 Добавить независимые ELF-фикстуры и эталоны, вручную созданные на
  уровне байтов и не использующие производственный анализатор, для
  `RPATH`/`RUNPATH`, наследования, токенов и некорректных мутаций. Добавить
  низкий `RLIMIT_NOFILE`, текущий
  открытый FD, превышение лимитов топологии/`argv`/аргументов, отсутствие
  memfd содержимого до проверки бюджета, мутацию при втором проходе, отсутствие
  утечек FD и тесты, подтверждающие, что очистка не скрывает первичную ошибку.
  Выполнено внутри exact four-suite: literal handcrafted ELF bytes/oracles,
  RPATH/RUNPATH/inheritance/tokens/DT_NULL, low/occupied FD и size caps,
  no-content-memfd pre-budget, second-pass mutation, FD leak и
  primary/cleanup preservation. Targeted red был `2 failed`; targeted green
  — `2 passed`. Два author exact run дали `146 passed, 0 failed` каждый без
  FLAKY; 22.7 намеренно остаётся открытым.
- [x] 22.7 Выполнить author validation: exact four-suite команду из 21.6 два
  раза подряд, локальную русскую проверку, strict OpenSpec,
  `git diff --check`, exact scope, line/ownership/resource/FD gates. Будущие
  зелёные author runs являются evidence, не independent acceptance.
  Требуемая финальная exact validation одновременно подтверждена независимой
  source-read-only парой на одном final snapshot: два дословных запуска без
  edits/retry/FLAKY дали `163 passed` (`31+43+44+45`) за `225.7s` и `223.1s`.
  Это validation evidence, а не author edits.
- [x] 22.8 Выполнить independent validation в `workspace-write` только для
  temp/cache/evidence при source-read-only policy: independent tests и exact
  four-suite два раза подряд, pre/post fingerprints, strict OpenSpec,
  `git diff --check`, exact scope. Получить accepted review без `BLOCK`.
- [x] 22.9 Только после accepted independent review закрыть truth-state
  задачи 19.4, 19.6, 19.7, 20.2, 21.2, 21.5–21.8 и связанные
  16.7/18.8/19.8/20.7/20.8, затем разрешить commit/push/task-owned PR.
  Live rollout/restart/process/MCP/DB/deploy/network требует отдельного exact
  разрешения и не открывается review, commit, push, PR или merge.
  Non-live truth-state полностью закрыт; это открывает только gate для
  commit/push/task-owned PR и не разрешает live rollout.

### 23. Minor remediation canonical invocation и trusted ELF hops

- [x] 23.1 Зафиксировать новый independent review `BLOCK` как historical
  evidence: inventory не перепроверял trusted roots после symlink hop,
  probe actual loader argv не проходил authoritative `SC_ARG_MAX`, а
  pre-acquisition resource plan использовал placeholder/file-only invocation.
  Это несоответствия уже одобренным requirements 22.x, не material delta;
  approval 2026-07-30 покрывает remediation. Independent/delivery truth
  остаётся открытой.
- [x] 23.2 Реализовать один substantive immutable canonical invocation owner
  для probe и production: полная topology directories/files/symlinks/perms,
  harness/anchors и FD roles. Pre-acquisition render использует
  worst-case legal decimal FD width из finite `RLIMIT_NOFILE`; actual render
  тем же spec обязан быть не больше bound и повторно проходит authoritative
  args/exec/FD checks перед соответствующим memfd/subprocess.
- [x] 23.3 Перепроверять lexical destination после каждого external ELF
  symlink hop против injectable trusted roots; absolute/relative escape,
  dangling target и cycle fail closed. Добавить valid-red regression tests:
  trusted-root escape, oversized actual probe argv и directory/symlink-heavy
  canonical topology с `memfd_calls == 0`. Targeted red: `3 failed`;
  targeted green: `3 passed`; полный sandbox author run: `45 passed`.
- [x] 23.4 Выполнить author exact four-suite два раза подряд без правок,
  strict OpenSpec, diff/line/fixture/provenance/scope gates. Закрыть только
  author validation claim после фактической проверки.
  Выполнено: два последовательных exact run дали `149 passed, 0 failed`
  каждый без retry/FLAKY и без правок между runs; strict OpenSpec valid,
  в отслеживаемых и неотслеживаемых файлах нет проблем с пробельными символами, лимиты `source`/`test`/`rollout`/`support`
  соблюдены, fixtures/provenance и exact task-owned scope проверены.
- [x] 23.5 Получить новый accepted independent review без `BLOCK`.
  Independent/delivery/live truth и commit/push/PR остаются открытыми.

### 24. Незначительное исправление acquisition/final handoff/ownership

- [x] 24.1 Зафиксировать latest independent `BLOCK` как historical evidence:
  acquisition peak, final handoff, canonical ownership, exact role order и
  symlink matrix не полностью реализовали уже одобренные requirements.
  Material scope не изменён; новый approval не нужен. Independent, delivery,
  commit/push/PR и live gates остаются открытыми.
- [x] 24.2 Добавить в `InventoryPlan` named strict acquisition temporary
  reserve из поддерживаемого `MAX_DIRECTORY_DEPTH` lifecycle; включить его в
  pre-content resource plan. Перед каждым subprocess после создания всех
  phase memfd выполнить authoritative exact final handoff check. Оставить
  invocation module единственным owner constants/base/production policy и
  отклонять missing/extra/reordered role maps до render/subprocess.
- [x] 24.3 Добавить targeted valid-red→green regressions: subprocess-isolated
  low-RLIMIT deep topology до первого content memfd без leak; late FD pressure
  без subprocess; exact role maps; temp-only relative multi-hop/absolute
  escape/relative escape/dangling/cycle symlink matrix с literal expected.
  Целевой red: role-map `6 failed`, late-pressure достиг запрещённого
  subprocess, deep acquisition не имел named bound; symlink valid path
  проходил, четыре unsafe cases fail-closed. Targeted green: `12 passed` и
  изолированный deep acquisition `1 passed`; набор sandbox `43 passed`.
- [x] 24.4 Выполнить author exact four-suite два раза подряд без edits/retry,
  строгую проверку OpenSpec, diff/no-index для неотслеживаемых файлов, подсчёт строк,
  fixtures/provenance/scope. Закрыть только после фактической проверки.
  Выполнено: два последовательных дословных запуска без edits/retry/FLAKY
  дали `161 passed, 0 failed` (`31+43+44+43`) за `210.3s` и `210.1s`.
  Строгая проверка OpenSpec успешна; проверки пробелов tracked/untracked и line-count,
  fixtures/provenance и exact task-owned scope gates пройдены.
- [x] 24.5 Получить новый accepted independent review без `BLOCK`.
  Independent/delivery/live truth и commit/push/PR остаются открытыми.

### 25. Историческое незначительное исправление согласованности топологии

- [x] 25.1 Зафиксировать новый independent P1 `BLOCK` как historical
  evidence существующего requirement: acquisition traversal не применял
  canonical `MAX_DIRECTORY_DEPTH`, поэтому mutation между inventory и
  acquisition могла создать content memfd до позднего topology mismatch.
  Новый material delta и approval не требуются.
- [x] 25.2 На время valid behavioral red переоткрыть claims 22.3, 22.5,
  24.2 и load-bearing часть 24.3. Regression вызвал настоящий
  `capture_bundle`, добавил после inventory topology глубже canonical cap при
  наличии лексически раннего regular file и завершился `Failed: DID NOT
  RAISE`, доказав implementation gap.
- [x] 25.3 До первого content memfd повторно выполнить canonical inventory с
  той же depth policy и exact сверить observed plan с approved
  `InventoryPlan`. Structured inventory/topology failure происходит до
  acquisition и не оставляет FD.
- [x] 25.4 Независимо применить импортированный из canonical inventory owner
  `MAX_DIRECTORY_DEPTH` в `_walk_directory`; проверять предел до открытия
  следующего directory на запрещённой глубине. Post-preflight mutation hook
  подтверждает fail-closed и отсутствие FD leak.
- [x] 25.5 Закрыть claims 22.3, 22.5, 24.2 и load-bearing часть 24.3 только
  после factual targeted green: два lifecycle regression и существующий
  second-pass mutation прошли, `3 passed in 6.95s`; relevant sandbox author
  run дал `45 passed за 219.2s`. Это author evidence, а не independent
  acceptance. Independent 22.8, 22.9, 23.5, 24.5, delivery и live gates
  остаются открытыми.
- [x] 25.6 После последней line-cap правки до `sealed_bundle` 999 и sandbox
  test 998 выполнены два clean exact four-suite именно на final snapshot:
  `163 passed` (`31+43+44+45`) за `225.7s` и `223.1s`, без
  правок и повторных запусков не было, FLAKY отсутствует.
- [ ] 25.7 Зафиксировать truth/evidence: read-only probe normal HOSTKEY shell
  и текущих MCP процессов показал finite soft `RLIMIT_NOFILE=1024`,
  hard `RLIMIT_NOFILE=1048576`; current sealed inventory plan требует 1360 FD,
  поэтому normal-shell exact run корректно fail-closed: `137 passed`,
  `26 dependent failed`, второй run не запускался. Никакой limit не менялся.
  Это не code defect по текущему requirement low-limit fail-closed, но
  production/live capacity не готова; любой raise/config/launcher/service
  environment change требует отдельного material approval и live gate.
  Commit/push/PR/merge/live/deploy/restart/MCP/DB остаются закрытыми.

### Принятый независимый обзор — синхронизация фактического состояния

Reviewer session `019fb2a2-d9d0-7df1-a228-845e7ec59b3f` завершилась verdict
`CODE VERDICT APPROVE`, findings отсутствуют. В review sandbox finite
`RLIMIT_NOFILE` soft/hard был `1048576/1048576`, required `1360`, limit не
изменялся. Strict OpenSpec, tracked/untracked whitespace, compile/static,
line/fixture/provenance/scope gates прошли. Raw diff fingerprint до и после
совпадает:
`c97370a82677a491c3eeb9f8279631010a06427a892e8073f7b1e03ce4f6c0cf`.
Это superseding acceptance для закрытых non-live truth-задач выше. Task 25.7
остаётся открытым: normal HOSTKEY/current MCP soft limit `1024` меньше required
`1360`, а material approval изменения environment capacity не получен.
