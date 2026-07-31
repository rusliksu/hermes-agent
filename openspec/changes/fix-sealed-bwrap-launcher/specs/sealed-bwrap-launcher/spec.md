## ADDED Requirements

### Requirement: Bubblewrap запускается ядром как проверенный executable

Система SHALL запускать probe и production preflight обычным kernel-level
exec канонического `/usr/bin/bwrap`, а не передавать `bwrap` как payload
явному dynamic loader. Непосредственно перед каждым handoff система SHALL
descriptor-relative с `O_NOFOLLOW` проверить, что executable является
обычным исполняемым файлом с `uid=0`, `gid=0`, не доступным для записи группе
или остальным, и что его `mode`, `device`, `inode`, `size` и SHA-256 совпадают
с sealed anchor, построенным до candidate execution. Утверждённый descriptor
SHALL оставаться открытым у parent до post-handoff verification и SHALL быть
передан child через executable handoff как отдельный canonical FD role.
Kernel exec target при этом SHALL оставаться literal canonical
`/usr/bin/bwrap`; descriptor является непрерывным identity/evidence anchor, а
не fd-based exec target. Любое расхождение SHALL завершать операцию
fail-closed.

#### Scenario: Probe использует normal executable handoff

- **WHEN** canonical capability probe готов к запуску
- **THEN** kernel exec target равен каноническому `/usr/bin/bwrap`
- **AND** launcher argv не содержит `ld-linux`, `--inhibit-cache`,
  `--preload` или sealed `bwrap` как payload loader
- **AND** verified executable identity совпадает с sealed anchor

#### Scenario: Утверждённый descriptor сохраняется через handoff

- **WHEN** pre-handoff verification `/usr/bin/bwrap` завершилась успешно
- **THEN** тот же открытый descriptor присутствует в canonical `pass_fds` во
  время kernel exec
- **AND** parent сохраняет descriptor открытым до обязательной post-handoff
  проверки
- **AND** literal `executable` subprocess равен `/usr/bin/bwrap`, а не
  `/proc/self/fd/*`, `fexecve` или explicit loader

#### Scenario: Production использует тот же launcher primitive

- **WHEN** authoritative candidate-specific preflight готов к запуску
- **THEN** он использует тот же verified normal executable handoff, что probe
- **AND** отличие между probe и production ограничено sealed
  `bwrap --args` payload и exact child command

#### Scenario: Подмена executable блокирует handoff или acceptance

- **WHEN** path, тип, `uid`, `gid`, `mode`, `device/inode/size` либо SHA-256
  `/usr/bin/bwrap` не совпадает с ожидаемым anchor
- **THEN** до handoff subprocess не создаётся, а после handoff его результат
  не принимается
- **AND** Python-only, explicit-loader, unsandboxed или частично sandboxed
  fallback отсутствует
- **AND** snapshot, wrapper и live runtime не изменяются

### Requirement: Sealed arguments и containment contract сохраняются

Система SHALL сохранить отдельные sealed immutable memfd для probe и
production `bwrap --args`, canonical namespace/mount profile, manifest-built
directory/symlink topology и sealed regular-file bindings candidate,
интерпретатора, trusted stdlib/runtime, harness и anchors. Система SHALL
передавать launcher только exact required FDs, а candidate child SHALL
запускаться с `-I -S -B` внутри containment до любого target import.

#### Scenario: Полный namespace profile сохраняется

- **WHEN** строится probe или production args payload
- **THEN** сохраняются `--unshare-user`, `--disable-userns`,
  `--unshare-pid`, `--unshare-ipc`, `--unshare-uts`, `--unshare-cgroup`,
  `--unshare-net`, `--new-session`, `--die-with-parent` и `--clearenv`
- **AND** сохраняются свежие `/proc`, минимальный `/dev`, раздельные tmpfs
  для `HOME`, `HERMES_HOME` и temp
- **AND** host sockets и mutable candidate backing directory не
  пробрасываются

#### Scenario: Sealed args нельзя изменить после anchor construction

- **WHEN** probe или production args сериализованы
- **THEN** payload находится в отдельном memfd с write/grow/shrink/future
  seals
- **AND** actual render не превышает prevalidated symbolic bounds,
  `SC_ARG_MAX`, named `bwrap --args` cap и finite `RLIMIT_NOFILE`
- **AND** final handoff повторно проверяет exact ordered roles, уникальность,
  открытое состояние и FD peak

#### Scenario: Production profile является authoritative

- **WHEN** baseline probe завершился успешно
- **AND** production invocation отклоняет bind sealed content, topology,
  namespace flag либо exact candidate argv
- **THEN** preflight завершается fail-closed
- **AND** успешный probe не считается доказательством полного production
  profile
- **AND** snapshot или switch replacement не выполняется

### Requirement: Launcher не наследует окружение или credentials

Система SHALL запускать `bwrap` с явно пустым environment и SHALL NOT
использовать `LD_PRELOAD`, `LD_LIBRARY_PATH`, ambient env, credential files,
sessions, tokens или secrets для нового handoff. Внутренний allowlist
candidate environment SHALL продолжать задаваться только через sealed
`bwrap --args` после `--clearenv`.

#### Scenario: Ambient env отсутствует

- **WHEN** probe или production subprocess создаётся
- **THEN** launcher получает exact empty environment
- **AND** ему передаются только проверенные FDs canonical invocation
- **AND** host env и credential paths не читаются и не отражаются в output

#### Scenario: Launcher failure остаётся fail-closed

- **WHEN** executable verification, resource check, subprocess creation,
  timeout, nonzero exit, bundle verification или FD cleanup завершается
  ошибкой
- **THEN** ошибка сохраняет primary и отдельные secondary/cleanup failures
- **AND** continuation к snapshot, switch, deploy или process replacement
  отсутствует

#### Scenario: Post-handoff verification выполняется на каждом terminal path

- **WHEN** child был фактически создан и затем завершился нормально, превысил
  timeout либо launcher получил `OSError` или иную ошибку после старта
- **THEN** после остановки и reap child система SHALL повторно проверить тот
  же executable descriptor и literal canonical path
- **AND** post-handoff mismatch или ошибка verifier сохраняется отдельным
  secondary failure, не заменяя исходный exit/timeout/`OSError`
- **AND** если исходной ошибки не было, post-handoff failure становится
  primary fail-closed причиной

#### Scenario: Ошибка до фактического старта не подделывает post-check

- **WHEN** subprocess не был фактически создан
- **THEN** система закрывает утверждённый descriptor и сохраняет первичную
  безопасную launch failure
- **AND** evidence не утверждает, что post-handoff verification выполнялась

### Requirement: Capture ограничен по байтам, а диагностика безопасна

Система SHALL захватывать `stdout` и `stderr` bubblewrap потоково с отдельными
жёсткими byte caps, не накапливая за пределами cap ни полный поток, ни
неограниченные chunks. При обнаружении первого байта сверх любого cap система
SHALL прекратить принятие результата, ограниченно остановить и reap child и
завершиться fail-closed с безопасным reason code
`bwrap_output_limit_exceeded`. Только
полностью прочитанный без overflow bounded `stderr` SHALL передаваться
allow-listed классификатору. Для сообщения
`setting up uid map: Permission denied` evidence SHALL сохранять причину
`bwrap_uid_map_setup_denied`; произвольный raw `stderr`, абсолютные пути,
FD numbers, env и secret-like values SHALL NOT включаться в пользовательскую
ошибку или rollout evidence.

#### Scenario: UID-map причина различима

- **WHEN** bubblewrap завершается с диагностикой отказа настройки UID map
- **THEN** fail-closed error содержит нормализованный
  `bwrap_uid_map_setup_denied`
- **AND** raw дочерний `stderr` не отражается

#### Scenario: Реальное переполнение capture блокирует continuation

- **WHEN** child фактически пишет в `stdout` или `stderr` больше
  соответствующего hard cap
- **THEN** сохранённые данные никогда не превышают cap
- **AND** child ограниченно завершается и reaped
- **AND** операция возвращает только `bwrap_output_limit_exceeded` без raw
  prefix, path, FD number или secret-like fragment
- **AND** post-handoff verification всё равно выполняется

#### Scenario: Неизвестный stderr остаётся безопасным

- **WHEN** bubblewrap возвращает неизвестный, бинарный, слишком длинный или
  содержащий path/secret-like fragment `stderr`
- **THEN** для полного bounded `stderr` evidence содержит только общий
  allow-listed reason и exit class, а для overflow — только
  `bwrap_output_limit_exceeded`
- **AND** raw bytes и выведенные из них значения не сохраняются
- **AND** классификатор никогда не вызывается с truncated либо unbounded
  данными

### Requirement: Acceptance доказывает launcher и отсутствие host mutation

Реализация SHALL пройти behavioral unit/invariant tests, отдельные exact
launcher/helper tests, HOSTKEY loader-vs-direct integration и существующий
five-module regression suite только через `scripts/run_tests.sh`. Проверки
SHALL использовать temp-only artifacts, пустой launcher env и ephemeral
`bwrap`/`true` child processes; они SHALL NOT читать credentials/env/secrets,
обращаться к live DB, заменять live wrapper/process, менять сервисы либо
изменять host network policy.

#### Scenario: Unit и helper tests проверяют exact handoff

- **WHEN** запускается focused launcher test module
- **THEN** он поведенчески проверяет normal executable target, отсутствие
  explicit loader/preload, sealed args, identity mismatch, FD cleanup,
  resource bounds и sanitized UID-map reason
- **AND** tests не читают production source text

#### Scenario: Независимые mutation tests разделяют security invariants

- **WHEN** запускается mutation/security suite
- **THEN** отдельные тесты мутируют digest при стабильной identity и identity
  при стабильном digest
- **AND** отдельные тесты мутируют `uid`, `gid`, `mode`, `device`, `inode`,
  `size` и поведение `O_NOFOLLOW` через syscall-level verifier doubles
- **AND** suite проверяет literal `executable=/usr/bin/bwrap`, сохранение
  утверждённого descriptor через handoff, timeout/`OSError` post-check и
  одновременное сохранение primary/secondary failures

#### Scenario: Capture overflow создаётся реальным producer

- **WHEN** security test запускает child, который пишет больше hard cap в
  реальный pipe `stdout` или `stderr`
- **THEN** test доказывает bounded memory/evidence и fail-closed reason без
  подмены результата синтетическим oversized объектом
- **AND** устаревшие assertions, требующие explicit-loader handoff, удалены
  либо явно помечены superseded и не участвуют в acceptance

#### Scenario: HOSTKEY integration различает launcher paths

- **WHEN** integration выполняется в том же HOSTKEY execution context, где
  authoritative direct profile доступен
- **THEN** control с explicit `ld-linux` воспроизводит UID-map denial
- **AND** новый normal direct launcher с теми же sealed args и полным
  namespace profile не возвращает UID-map denial и завершается успешно
- **AND** pre/post oracle подтверждает отсутствие DB, credential, wrapper,
  service, persistent-process и host-policy mutation

#### Scenario: Existing regression suite остаётся зелёным

- **WHEN** exact five-module suite запускается с
  `HERMES_TEST_FILE_RETRIES=0`
- **THEN** bootstrap, rollout, runtime coherence, runtime sandbox и rollout
  state modules проходят без retry или `FLAKY`
- **AND** pre/post source fingerprints совпадают

### Requirement: Delivery и live rollout разделены approval gates

Approval этого baseline SHALL разрешать только valid red, минимальную
implementation и repo-local/temp-only verification. После accepted review
первой delivery единицей SHALL быть task-owned non-live PR. Merge SHALL NOT
разрешать deploy, restart или live mutation; live `prepare`, `switch`,
dedicated process replacement и rollback SHALL требовать отдельных exact
approval gates.

#### Scenario: Code gate заканчивается PR

- **WHEN** реализация, tests, strict OpenSpec и review приняты
- **THEN** может быть создан только task-owned non-live PR
- **AND** deploy, restart, wrapper switch, DB/service/process mutation не
  выполняются в code gate

#### Scenario: Live gates выполняются последовательно

- **WHEN** PR слит и требуется live rollout
- **THEN** отдельно одобряются `prepare` dry-run, `prepare --apply`,
  `switch` dry-run, `switch --apply` и замена dedicated MCP process
- **AND** каждый следующий gate получает exact plan/hash/identity evidence
- **AND** rollback использует ранее проверенные stable wrapper bytes/mode и
  previous process только по отдельному approval
