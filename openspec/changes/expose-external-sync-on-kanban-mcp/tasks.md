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

## 7. Сохранённый high-level гейт доставки после PR #15

- [ ] 7.1 После merge отдельно запросить одобрение на новый immutable runtime
  из merge SHA и зафиксировать rollback target.
- [ ] 7.2 Только после отдельного одобрения точечно переключить MCP wrapper и
  запустить новый MCP process; не менять глобальный Hermes symlink, не
  перезапускать Hermes/Gurra и не переносить dirty Telegram patch.

## 8. Гейт material delta отдельного helper PR

- [x] 8.1 Зафиксировать явное одобрение material delta: отдельный helper PR
  без live rollout; текущий запуск — только planning/OpenSpec.
- [x] 8.2 Перед реализацией повторно подтвердить task-owned worktree/branch,
  exact base `062f2f0f1f6947830d1b222a3ef470e145a7c34d`, чистый status и
  отсутствие material изменения standalone layout; при расхождении
  остановиться и оформить новый delta.

## 9. Отдельный helper PR

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

## 10. Temp-only tests и проверки helper PR

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

## 11. Точный live gate после merge helper PR

- [ ] 11.1 Отдельно запросить одобрение exact dry-run plan с current/candidate
  полными Git SHA, текущим SHA-256 wrapper, путями runtime/state/wrapper,
  candidate path и snapshot ID; approval helper PR не считается live
  approval.
- [ ] 11.2 После одобрения повторить `prepare` без `--apply`, сопоставить plan
  с одобренными exact values и остановиться при любом расхождении.
- [ ] 11.3 Выполнить `prepare --apply`; проверить manifest hashes, current
  rollback target, candidate exact HEAD/tracked cleanliness и candidate
  interpreter. Stable wrapper/process/DB ещё не менять.
- [ ] 11.4 Повторить `switch` без `--apply`, проверить stale-wrapper guard и
  только затем выполнить `switch --apply`.
- [ ] 11.5 Запустить или заменить только standalone Kanban MCP process;
  глобальный Hermes symlink, Hermes/Gurra processes, services, Windows
  config, dirty Telegram patch и live DB не менять.
- [ ] 11.6 Выполнить bounded MCP `initialize`, exact `tools/list` 2/11 и
  `kanban_sync_external_task` с `dry_run=true`; подтвердить отсутствие live
  DB writes.
- [ ] 11.7 При провале switch/process/smoke выполнить сначала `rollback` без
  `--apply`, затем `rollback --apply`, вернуть только предыдущий standalone
  MCP process и повторить bounded smoke.
- [ ] 11.8 Сохранить evidence exact plan/manifest/hashes/process/smoke или
  rollback outcome; candidate и snapshot не удалять автоматически.
