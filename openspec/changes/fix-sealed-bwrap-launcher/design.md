## Context

Authoritative base этого change —
`66621f994fac28ed3a5c6d05b25dc0c85a8a317e`. Доказательство blocker
находится в
`/home/openclaw/codex-state/artifacts/20260731T-bwrap-diagnosis-MQZJCd/REPORT.md`.
Обычный `/usr/bin/bwrap 0.9.0` с полным профилем пространств имён и sealed
`--args` проходит в исходном HOSTKEY execution context. Тот же executable,
запущенный как payload explicit `ld-linux`, завершается раньше на
`bwrap: setting up uid map: Permission denied`.

Текущий вызывающий путь:

1. `scripts/hermes_kanban_mcp_rollout.py::_run_prepare` и transition path
   входят в
   `scripts/hermes_kanban_mcp_runtime_coherence.py::import_preflight_session`
   до snapshot или wrapper replacement.
2. `_parent_trust_bundle` строит immutable sealed content bundle и canonical
   invocation.
3. `_run_import_preflight` вызывает
   `scripts/hermes_kanban_mcp_os_sandbox.py::run`, который сначала выполняет
   `_probe`, затем authoritative production invocation.
4. `scripts/hermes_kanban_mcp_invocation.py::_launcher` сейчас формирует
   `/proc/self/fd/<loader> --inhibit-cache --preload
   /proc/self/fd/<libraries> /proc/self/fd/<bwrap> --args <sealed-fd>`.
5. `_probe` заменяет любой nonzero exit общей ошибкой и теряет точную
   UID-map причину.

Активный change `expose-external-sync-on-kanban-mcp` ранее закрепил
исполнение memfd-копии `bwrap` через explicit loader как часть sealed-byte
contract. Доказанный HOSTKEY blocker показывает, что это load-bearing
архитектурное допущение неверно. Настоящий change отдельный, не редактирует
старые artifacts и требует нового material approval.

После baseline implementation независимый read-only security/code review
`20260731T125236Z-sealed-launcher-independent-security-rev` модели
`gpt-5.6-sol` завершился с `verdict=BLOCK`. Review установил, что
`subprocess.PIPE` сам по себе не является byte bound, exception paths не
гарантируют post-handoff verification, verifier не фиксирует `gid` и не
проводит утверждённый descriptor через handoff, а tests недостаточно
независимо мутируют security invariants. Настоящий material delta устраняет
эти пробелы только на уровне планирования и ещё не одобрен.

В managed Codex sandbox planning probes normal path и FD variants прошли
стадию UID map, но остановились позже на запрете `NETLINK_ROUTE`; этот
результат подтверждает узость исходного UID-map blocker, но не является
HOSTKEY acceptance полного profile. Нормативным доказательством остаётся
исходный read-only report и будущая integration в том же execution context.

## Goals / Non-Goals

**Цели:**

- Устранить explicit-loader UID-map blocker минимальным normal kernel exec
  канонического проверенного `bwrap`.
- Сохранить sealed candidate/runtime content, sealed `bwrap --args`, полный
  namespace/mount profile, empty launcher env и fail-closed semantics.
- Сохранить `bwrap_sha256` как parent-owned sealed anchor и сверять с ним
  actual executable непосредственно перед handoff.
- Ограничить фактический capture `stdout`/`stderr` жёсткими byte caps и дать
  sanitized evidence только из полного bounded `stderr`.
- Выполнять post-handoff verification на normal exit, timeout и каждой
  ошибке после фактического старта, не теряя primary или secondary evidence.
- Проверять `uid` и `gid` вместе с `mode/device/inode/size/SHA` и проводить
  утверждённый descriptor через handoff при сохранении literal canonical
  path exec.
- Доказать изменение behavioral tests и HOSTKEY integration, не затрагивая
  live DB/service/process/runtime.
- Доказать security invariants независимыми syscall-level mutation tests и
  реальным producer overflow, а не source-text assertions или synthetic
  completed-process doubles.
- Сохранить раздельные code, prepare, switch, process и rollback gates.

**Не входит в цели:**

- Не менять sysctl, AppArmor policy, `RLIMIT_NOFILE`, network/DNS, systemd,
  сервисы, БД, credentials, env files, live wrapper или stable runtime.
- Не обновлять и не заменять system package `bubblewrap`.
- Не ослаблять `--disable-userns`, namespaces, mounts, sealed candidate
  content, Python guards или origin evidence.
- Не добавлять Python-only/unsandboxed fallback, daemon, privileged helper,
  setuid binary, native dependency или новый public API.
- Не выполнять commit, push, PR, deploy, restart, `prepare`, `switch` или
  process replacement в planning run.
- Не менять schema v3 snapshot/wrapper contract и не редактировать
  `expose-external-sync-on-kanban-mcp`.

## Decisions

### 1. Exact primitive — normal exec проверенного `/usr/bin/bwrap`

Canonical command для probe и production начинается с
`/usr/bin/bwrap --args <sealed-fd>`. `subprocess.run` получает exact
`executable=/usr/bin/bwrap`, `env={}` и canonical `pass_fds`; kernel
загружает ELF обычным путём. Explicit `ld-linux`, `--inhibit-cache` и
`--preload` полностью отсутствуют.

Перед каждым subprocess owner descriptor-relative открывает final component
`bwrap` с `O_RDONLY|O_NOFOLLOW|O_CLOEXEC`, проверяет по этому же descriptor
regular executable, `uid=0`, `gid=0`, executable mode, отсутствие group/other
write, `device`, `inode`, `mode`, `size` и SHA-256 против anchor из sealed
bundle. Hash вычисляется из проверенного descriptor, а не повторным открытием
path. Отдельная path verification подтверждает, что literal
`/usr/bin/bwrap` всё ещё указывает на ту же identity.

Утверждённый executable descriptor становится отдельной canonical FD role.
Parent удерживает его от pre-check до завершения post-check, а `pass_fds`
снимает `CLOEXEC` только в child на момент handoff, так что тот же descriptor
существует при kernel exec. Kernel target всё равно задаётся literal
`executable=/usr/bin/bwrap`: descriptor служит непрерывным identity/evidence
anchor и не подменяет path-based exec. Role входит в symbolic/actual FD
budgets и final handoff verification.

После любого фактического старта parent сначала ограниченно завершает и reap
child, затем повторно проверяет удерживаемый descriptor и canonical path.
Проверка выполняется для success, nonzero, timeout, capture overflow и
`OSError`/другой ошибки после старта. Любое расхождение обесценивает результат
и завершает preflight fail-closed. Descriptor cleanup сохраняет
primary/secondary/cleanup failure contract.

Это минимальный доказанный вариант: normal path полностью прошёл исходную
HOSTKEY матрицу, а explicit loader воспроизводимо не прошёл UID map.
Kernel FD-exec через `fexecve`/`execveat` не выбирается, потому что literal
canonical `/usr/bin/bwrap` является частью доказанного path-sensitive
AppArmor/LSM behavior, а полный FD-exec профиль не доказан в исходном
execution context. Переход на fd-based exec после approval считается новым
material delta и требует exact path-vs-FD acceptance.

Trade-off: actual `bwrap` и его dynamic loader closure загружаются из
root-owned canonical system paths, а не исполняются из memfd-копии и не
связываются с kernel exec через `fexecve`/`execveat`. Descriptor-spanning
handoff доказывает непрерывность проверенного anchor и обнаруживает path
mutation post-factum, но не может доказать, что при враждебной root-level
гонке kernel исполнил именно этот inode. Root-level concurrent host mutation
остаётся вне unprivileged rollout threat model. Взамен сохраняется
path-sensitive kernel/AppArmor behavior с HOSTKEY evidence. Это material
decision и отдельный approval gate; документ не выдаёт descriptor retention
за fd-bound execution.

Отклонённые варианты:

- Explicit loader с другими flags: не устраняет доказанный execution-shape
  blocker и сохраняет неверную модель `/proc/self/exe`.
- Sysctl/AppArmor/package/RLIMIT workaround: шире scope, меняет host policy
  и не нужен, поскольку normal profile уже доказан.
- Python-only или частичный sandbox fallback: разрушает security boundary.
- `LD_PRELOAD`/`LD_LIBRARY_PATH`: добавляет launcher environment, усложняет
  closure semantics и нарушает no-env contract.
- Новый native exec helper: новая supply-chain/build surface без
  необходимости для доказанного normal path.
- `fexecve`/`execveat` утверждённого descriptor: сильнее связывает identity с
  exec, но меняет literal path/LSM semantics и не имеет требуемого HOSTKEY
  evidence в этом change.

### 2. Canonical invocation остаётся единым owner budget и execution

`scripts/hermes_kanban_mcp_invocation.py` остаётся единственным owner формы
probe/production argv, symbolic FD width, `SC_ARG_MAX`, `bwrap --args` cap и
exact role order. `_launcher` заменяется direct launcher representation.

Probe role order больше не содержит launcher-only `loader/library/bwrap`
roles; он содержит exact sealed args и отдельный утверждённый executable
descriptor, необходимый descriptor-spanning handoff verification. Production
role order продолжает содержать ordered content roles, harness, anchors и
production args.
Одинаковый immutable spec используется при раннем plan и actual render.
Никакой placeholder invocation или дублирующий argv builder не добавляется.

`bwrap`, loader и library entries могут оставаться частью sealed content
manifest там, где они нужны существующему candidate/runtime closure и
`bwrap_sha256` anchor. Удаление capture entries, ELF inventory либо snapshot
fields не входит в минимальный change. Если implementation докажет, что для
корректного resource plan требуется изменить topology/capture ownership, это
material delta и новое approval, а не попутная cleanup.

### 3. Ответственность модулей

- `scripts/hermes_kanban_mcp_invocation.py` владеет direct canonical argv,
  role order и symbolic/actual resource bounds.
- `scripts/hermes_kanban_mcp_os_sandbox.py` владеет executable verification,
  exact subprocess handoff, byte-bounded capture, empty env, `stderr`
  classification и primary/secondary/cleanup errors.
- `scripts/hermes_kanban_mcp_sealed_bundle.py` сохраняет sealed content и
  `bwrap_sha256` anchor; ожидается отсутствие правок. Любая неизбежная правка
  ограничивается данными anchor, без изменения candidate-content promise.
- `scripts/hermes_kanban_mcp_resources.py` меняется только если direct
  canonical spec выявит фактическую ошибку расчёта; policy и named limits не
  расширяются.
- `scripts/hermes_kanban_mcp_runtime_coherence.py` сохраняет текущий flow и
  не получает launcher policy. Нормализованный reason проходит через
  существующий `SandboxError`/`RolloutError` transport.
- Новый
  `tests/scripts/test_hermes_kanban_mcp_sealed_launcher.py` владеет focused
  launcher/helper behavioral tests, чтобы не превышать существующие line
  caps `runtime_coherence.py`, `runtime_sandbox.py` и их tests.

### 4. Byte-bounded capture и безопасный classifier

`subprocess.PIPE` не ограничивает объём, который `communicate()` может
накопить в памяти. Поэтому `os_sandbox` переходит на `Popen` и единый
потоковый drain обоих pipes под общим deadline. Named constants
`BWRAP_STDOUT_CAPTURE_LIMIT=65536` и
`BWRAP_STDERR_CAPTURE_LIMIT=65536` задают отдельный hard cap в байтах для
каждого stream. Реализация читает bounded chunks, сохраняет только часть до
оставшегося cap и при обнаружении первого байта сверх cap больше не расширяет
capture buffer.

Overflow становится primary fail-closed reason
`bwrap_output_limit_exceeded`. Owner ограниченно посылает terminate, затем
kill при необходимости, продолжает безопасно drain/reap без роста capture и
после reap выполняет post-handoff verification. В exception/evidence не
попадают stream name, raw prefix, decoded fragments, path, FD number или
secret-like values. Если одновременно возникает timeout, wait/kill,
post-check или cleanup failure, первоначально обнаруженная причина остаётся
primary, а остальные сохраняются как secondary failures.

Classifier получает `stderr` только если EOF достигнут без overflow и
buffer не превышает hard cap. Маленькая pure function сопоставляет только
allow-listed case-insensitive signatures со стабильными reason codes.
Обязательный code — `bwrap_uid_map_setup_denied`; неизвестные полные bounded
данные получают общий code с exit class. Truncated/overflow data никогда не
классифицируются. Raw output не входит в exception/evidence.

Альтернатива `subprocess.run(capture_output=True)` отклонена: она проще, но
не задаёт memory bound. Обрезать output после `communicate()` тоже
недостаточно, потому что unbounded allocation уже произошёл. Выбранные caps
и reason code являются частью material delta и требуют approval.

### 5. Post-handoff state machine и составные failures

`os_sandbox` явно различает `child_started=False` до успешного возврата
`Popen` и `child_started=True` после него. Для каждого started child один
`finally`-owner гарантирует bounded stop/reap, post-check удерживаемого
descriptor и canonical path, затем cleanup. Это одинаково применяется к
normal zero/nonzero exit, timeout, capture overflow, pipe/wait `OSError` и
любой иной ошибке после старта. `Popen` failure до создания child не
маркируется как post-handoff path.

Failure model сохраняет первое событие как primary. Ошибка post-check после
существующей primary добавляется отдельным безопасным secondary code и не
заменяет exit class, timeout, overflow или `OSError`; при отсутствии primary
она сама становится primary. Cleanup failures добавляются после secondary и
не стирают обе предыдущие категории. Evidence таким образом позволяет
доказать и исходную runtime-причину, и последующую утрату executable trust,
не включая raw exception text дочернего процесса.

### 6. Независимые tests и acceptance

Focused tests должны исполнять code, а не читать source text:

- literal canonical `executable=/usr/bin/bwrap` и exact argv для
  probe/production;
- отсутствие loader/preload и ambient env;
- sealed args, executable descriptor role, exact role order, FD budget и
  final handoff;
- отдельные type/`uid`/`gid`/`mode`/`O_NOFOLLOW`, identity и digest mismatch
  до subprocess;
- normal/nonzero, timeout, post-start `OSError`, post-check и cleanup failure
  без continuation и без потери primary/secondary evidence;
- allow-listed UID-map reason, redaction неизвестного bounded `stderr` и
  реальное переполнение каждого pipe сверх hard cap.

Verifier tests подменяют syscall boundary (`os.open`/descriptor-relative
open, `os.fstat`, descriptor reads/hash и path identity lookup), а не сам
verifier целиком. Digest-vs-identity mutations разделены: один test меняет
bytes при стабильных metadata, другой меняет `device/inode/size` при
стабильном ожидаемом digest. `uid`, `gid`, `mode` и отсутствие
`O_NOFOLLOW` проверяются независимыми mutations, чтобы одна guard не маскировала
другую.

Capture test запускает реальный ephemeral producer, который пишет больше
hard cap в настоящий pipe. Synthetic `CompletedProcess` с oversized bytes не
является достаточным доказательством memory bound. Timeout и post-start
`OSError` doubles обязаны доказывать post-check call count/order и совместное
сохранение primary/secondary failures. Старые tests, нормативно требующие
explicit-loader argv или sealed memfd как exec target, удаляются либо явно
помечаются superseded и исключаются из acceptance; loader остаётся только как
намеренный HOSTKEY control.

HOSTKEY integration использует ephemeral sealed args memfd и `/usr/bin/true`.
Она сравнивает explicit-loader control и новый normal direct path с exact
одинаковым namespace profile. Acceptance требует, чтобы direct path
завершился успешно и не содержал UID-map denial. Managed Codex sandbox,
который запрещает следующий network namespace step, не используется как
ложная full-profile acceptance.

После focused tests два последовательных запуска existing five-module suite
выполняются через `scripts/run_tests.sh` с
`HERMES_TEST_FILE_RETRIES=0`, без retry/`FLAKY`, с одинаковыми pre/post
source fingerprints. Все artifacts находятся в temp; live DB, credentials,
wrapper, runtime, services и persistent processes не читаются и не
изменяются. Допустимы только ephemeral test child processes.

### 7. Rollout и rollback gates

Baseline approval не распространяется на настоящий material delta. Только
повторное явное approval открывает его implementation и local/temp
verification. После accepted повторного review без `BLOCK` создаётся
отдельный non-live PR. Code gate не включает deploy или restart.

После merge live workflow требует отдельных exact approvals:

1. `prepare` dry-run с plan/hash/identity evidence;
2. `prepare --apply`;
3. `switch` dry-run;
4. `switch --apply`;
5. замена только dedicated MCP process и bounded smoke.

Rollback отдельно проверяет и восстанавливает exact stable wrapper
bytes/mode и previous dedicated process. Merge или любой предыдущий gate не
разрешает следующий.

## Risks / Trade-offs

- [Material relaxation sealed executable] → sealed `bwrap` bytes становятся
  anchor, а normal exec использует root-owned canonical path; `uid`, `gid`,
  `mode`, identity и digest проверяются до и после, privileged host mutation
  явно вне threat model.
- [Path/LSM behavior зависит от execution context] → exact HOSTKEY
  loader-vs-direct integration обязательна; managed sandbox result не
  подменяет acceptance.
- [TOCTOU между проверкой path и kernel exec] → сохраняется открытый anchor
  descriptor и проводится через handoff, но literal path exec не становится
  fd-bound; post-exec identity обнаруживает mutation, а расширение threat
  model до hostile concurrent root требует отдельного material design и
  вероятного `fexecve`/`execveat`.
- [Resource plan рассинхронизируется после удаления launcher roles] → один
  canonical spec строит symbolic и actual render; low-FD/SC_ARG_MAX/final
  handoff tests остаются load-bearing.
- [Child исчерпает память unbounded output] → потоковый dual-pipe drain,
  отдельные hard caps, bounded terminate/kill/reap и real-producer tests.
- [Диагностика утечёт raw output] → classifier получает только полный bounded
  `stderr`; overflow и unknown output дают только allow-listed reason codes.
- [Post-check потеряет исходную причину] → единый started-child `finally`
  сохраняет primary, secondary post-check и cleanup failures раздельно.
- [Scope расползётся в старый active change] → этот change не редактирует
  `expose-external-sync-on-kanban-mcp`; consolidation выполняется только
  после approval и отдельного review решения.

## Migration Plan

1. Получить повторное явное approval настоящего material delta; прежнее
   baseline approval его не покрывает.
2. Выполнить новые valid red/mutation tests и минимальную delta-реализацию
   только в task-owned
   worktree.
3. Пройти focused/mutation tests, HOSTKEY integration, два five-module run,
   Russian consistency, strict OpenSpec, diff/scope/line gates и повторный
   independent review без `BLOCK`.
4. Создать non-live task-owned PR; не выполнять deploy/restart.
5. После merge проходить live prepare/switch/process gates только по
   отдельным exact approvals.
6. При любой ошибке до live switch оставить current stable runtime/wrapper
   неизменными; после отдельно одобренного switch использовать snapshot-owned
   exact rollback.

## Open Questions

- Принимается ли material trade-off: literal normal exec root-owned
  `/usr/bin/bwrap` с descriptor-spanning sealed anchor обнаруживает
  post-handoff mutation, но не даёт fd-bound гарантии против concurrent
  hostile root?
- Принимаются ли hard caps `65536` bytes отдельно для `stdout` и `stderr` и
  общий безопасный overflow code `bwrap_output_limit_exceeded`?
- В каком exact HOSTKEY executor следует выполнять full-profile integration,
  чтобы он совпадал с исходным read-only evidence и не наследовал managed
  Codex network restriction?
- Следует ли после approval пометить launcher-specific clauses старого
  active change как superseded отдельным planning delta или сохранить
  настоящий change единственным нормативным owner до архивирования? Любой
  вариант не разрешает редактировать старый change в текущем planning run.
