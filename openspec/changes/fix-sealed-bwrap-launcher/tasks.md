## 1. Явный material approval

Отмеченные ниже baseline-задачи фиксируют уже выполненную работу, но не
считаются выполнением или одобрением material delta после review
`20260731T125236Z-sealed-launcher-independent-security-rev`.

- [x] 1.1 Получить явное одобрение proposal/spec/design/tasks change
  `fix-sealed-bwrap-launcher`, включая trade-off normal exec root-owned
  `/usr/bin/bwrap` с sealed digest anchor вместо explicit-loader исполнения
  memfd-копии; до approval не менять source, tests или fixtures.
- [x] 1.2 Перед valid red повторно подтвердить task-owned worktree/branch,
  exact base `66621f994fac28ed3a5c6d05b25dc0c85a8a317e`, scope старого active
  change и отсутствие material новых данных; при расхождении оформить
  planning delta и получить повторное approval.
- [x] 1.3 Получить повторное явное approval material delta по
  byte-bounded capture, exception-path post-check, `gid` и
  descriptor-spanning literal-path handoff, составному failure evidence и
  независимым mutation/security tests; прежнее baseline approval этот scope
  не разрешает.

## 2. Valid red и exact launcher contract

- [x] 2.1 Добавить отдельный behavioral test module
  `tests/scripts/test_hermes_kanban_mcp_sealed_launcher.py`, не читающий
  production source text, и получить valid red на текущем explicit-loader
  argv для probe и production.
- [x] 2.2 Зафиксировать red tests normal executable target, отсутствия
  `ld-linux`/`--inhibit-cache`/`--preload`, exact empty env, sealed args и
  одинакового launcher primitive для probe/production.
- [x] 2.3 Зафиксировать red tests fail-closed до subprocess при неверном
  type/owner/mode/identity/SHA-256 bwrap, а также primary/cleanup failure и
  FD leak oracles.
- [x] 2.4 Зафиксировать red tests allow-listed
  `bwrap_uid_map_setup_denied` и redaction неизвестного, бинарного,
  path/secret-like или oversized stderr без сохранения raw bytes.

## 3. Минимальная реализация

- [x] 3.1 Изменить только canonical launcher model в
  `scripts/hermes_kanban_mcp_invocation.py`: normal
  `/usr/bin/bwrap --args <sealed-fd>`, единый symbolic/actual render,
  обновлённый exact role order и прежние named FD/argv/args limits.
- [x] 3.2 В baseline `scripts/hermes_kanban_mcp_os_sandbox.py` реализовать
  descriptor-relative `O_NOFOLLOW` проверку root-owned/non-writable
  executable против sealed identity/SHA-256 до subprocess и после обычного
  возврата, а также exact normal `executable=/usr/bin/bwrap` handoff с
  `env={}`; непокрытые exception paths вынесены в 3.8.
- [x] 3.3 В том же baseline owner добавить allow-listed stderr classifier над
  полученным capture, сохранив существующий `SandboxError`/`RolloutError`
  transport, fail-closed control flow и отдельные cleanup failures;
  фактический byte bound вынесен в 3.7.
- [x] 3.4 Не менять `runtime_coherence.py`, sealed bundle, resource planner
  или snapshot fields без доказанной неизбежности; любое изменение
  content/ELF capture ownership или executable primitive оформить как
  material delta и получить повторное approval.
- [x] 3.5 Проверить line/ownership gates: никакой новый thin wrapper,
  `runtime_coherence.py <=900`, каждый source/test `<1000`, новый test owner
  содержательный и production API/dependencies/schema не изменены.
- [x] 3.6 Расширить executable verifier и canonical invocation: независимо
  проверять `uid=0`, `gid=0`, `mode`, `device`, `inode`, `size` и SHA-256 из
  того же `O_NOFOLLOW` descriptor; добавить отдельную executable descriptor
  FD role, сохранить её у parent до post-check и провести через `pass_fds`,
  оставив literal `executable=/usr/bin/bwrap`.
- [x] 3.7 Заменить unbounded `subprocess.PIPE` accumulation на потоковый
  dual-pipe capture с hard caps `65536` bytes отдельно для `stdout` и
  `stderr`, bounded terminate/kill/reap и безопасным fail-closed reason
  `bwrap_output_limit_exceeded`; классифицировать только полный bounded
  `stderr`.
- [x] 3.8 Ввести единый started-child exception/finally path: выполнять
  post-handoff descriptor/path verification после normal/nonzero exit,
  timeout, capture overflow и `OSError`/ошибки после фактического старта;
  сохранять primary, secondary post-check и cleanup failures без взаимной
  потери evidence.

## 4. Focused и integration acceptance

- [x] 4.1 Запустить focused launcher/helper tests только через
  `scripts/run_tests.sh` с `HERMES_TEST_FILE_RETRIES=0`; получить green без
  retry/`FLAKY` и без artifacts вне temp.
- [x] 4.2 Запустить связанные unit/invariant tests canonical invocation,
  low-FD, `SC_ARG_MAX`, sealed args, final handoff, identity mismatch,
  timeout/nonzero и cleanup failure; подтвердить отсутствие source-text
  assertions.
- [x] 4.3 В exact HOSTKEY execution context выполнить read-only
  loader-vs-direct integration с одинаковым полным namespace profile и
  sealed args: explicit-loader control воспроизводит UID-map denial, новый
  normal direct launcher завершается успешно и не возвращает UID-map denial.
- [x] 4.4 Для integration зафиксировать pre/post oracle: live wrapper/runtime,
  DB, credential/auth files, services, host network policy и persistent MCP
  processes не читаются и не изменяются; создаются только ephemeral
  `bwrap`/`true` child processes и temp/evidence artifacts.
- [x] 4.5 Добавить независимые syscall-level mutation/security tests:
  literal canonical `/usr/bin/bwrap`, verifier doubles на границах
  `open`/`fstat`/descriptor hash/path identity, раздельные digest-vs-identity
  mutations и отдельные `uid`/`gid`/`mode`/`O_NOFOLLOW` mutations.
- [x] 4.6 Добавить отдельные tests normal exit/timeout/post-start `OSError`
  с обязательным post-check и совместным primary/secondary evidence, а также
  реальный ephemeral producer overflow для каждого capture pipe; удалить или
  явно пометить superseded устаревшие explicit-loader assumptions.

## 5. Existing regression и review

- [x] 5.1 На одном final snapshot дважды подряд выполнить exact five-module
  suite с `HERMES_TEST_FILE_RETRIES=0`:
  `test_hermes_kanban_mcp_bootstrap.py`,
  `test_hermes_kanban_mcp_rollout.py`,
  `test_hermes_kanban_mcp_runtime_coherence.py`,
  `test_hermes_kanban_mcp_runtime_sandbox.py` и
  `test_hermes_kanban_mcp_rollout_state.py`.
- [x] 5.2 Подтвердить оба run без retry/`FLAKY`, равенство pre/post source
  fingerprints, отсутствие live DB/credential/wrapper/runtime/service
  access и отсутствие новых dependencies или migrations.
- [ ] 5.3 Выполнить Russian consistency, `openspec validate
  fix-sealed-bwrap-launcher --strict --no-interactive`, `git diff --check`,
  exact diff scope и independent security/code review без `BLOCK`.

  Примечание: strict validation, `git diff --check`, exact diff scope и independent review с verdict `ACCEPTED` выполнены; Russian consistency оставлена незакрытой, потому что `Test-OpenSpecRussian.ps1` и `pwsh` отсутствуют в HOSTKEY.

## 6. Только non-live PR

- [x] 6.1 После зелёной acceptance и accepted review получить отдельное
  разрешение delivery и создать task-owned non-live PR только с launcher,
  focused tests и этим OpenSpec change.
- [x] 6.2 В PR явно указать, что merge не разрешает deploy, restart,
  `prepare`, `switch`, wrapper/runtime/DB/service/process mutation или
  Kanban-карточки.

## 7. Отдельные live и rollback gates после merge

- [ ] 7.1 После merge отдельно запросить approval на exact `prepare` dry-run
  и показать plan/hash/identity evidence; merge не является approval.
- [ ] 7.2 Отдельно запросить approval на `prepare --apply`; до него candidate
  и snapshot не создавать.
- [ ] 7.3 Отдельно запросить approval на `switch` dry-run, затем отдельное
  approval на `switch --apply`; до последнего stable wrapper не менять.
- [ ] 7.4 Отдельно запросить approval на замену только dedicated MCP process
  и bounded smoke; не выполнять deploy, global restart, DB write, systemd,
  network или host-policy mutation.
- [ ] 7.5 При неуспехе использовать snapshot-owned exact rollback
  wrapper bytes/mode и previous dedicated process только по отдельному
  approval; каждый live gate сохраняет возможность остановиться на current
  stable runtime.
