> **Текущий статус REMEDIATION BASELINE ПОСЛЕ BLOCK:** независимое ревью
> дельты 26.x завершилось `BLOCK`. Реализация 26.3–26.5 и run
> `128 passed` остаются только historical evidence, superseded и не приняты
> как текущая acceptance. Baseline 27.x явно одобрен точной фразой
> «одобряю material remediation baseline 27.x», valid red и минимальная
> repo-local implementation 27.3–27.4 выполнены. Один exact five-module
> author run дал `180 passed`; 27.5+, commit/push/PR и live-действия не
> разрешены.

## Почему

PR #15 доставил выделенную Kanban MCP поверхность, PR #16 — guarded
dry-run-first helper `prepare/switch/rollback`, а bootstrap-helper затем
создал Git baseline из non-Git export runtime. Последующий rollout
baseline→target был откатан: wrapper для snapshot
`6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`
успешно восстановлен с `exit 0` до SHA-256
`17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`;
restart, process replacement, DB, Kanban и smoke не выполнялись.

Root cause отката: candidate venv был скопирован из baseline/export и при
запуске `python -m hermes_cli.main` импортировал `hermes_cli` и
`agent.transports.hermes_kanban_mcp_server` из старого `site-packages`, тогда
как target source checkout уже содержал новый
`kanban_sync_external_task`. Поэтому wrapper на candidate path сам по себе не
доказывал, что runtime исполняет target source tree.

Нужен новый material repair baseline без implementation и live actions. Он
должен заменить будущий rollout contract на source-cwd-aware wrapper и
import-origin preflight, не используя network, `pip`, editable install или
файлы `.pth` и сохраняя совместимость rollback для уже существующих schema v2
snapshots.

Три последовательных независимых ревью завершились `BLOCK`. Последнее,
`20260729T161437Z-kanban-runtime-coherence-final-review`, подтвердило, что
исправленные sticky denial, synthetic homes, точная wrapper grammar и
snapshot-only rollback недостаточны: Python audit/blocklist не удерживает
`subprocess._fork_exec`, `resource.prlimit` и native syscalls, а policy
начинает действовать только после запуска потенциально подменённого candidate
interpreter. Оно также потребовало независимый исторический schema-v2 golden
и разгрузку 996-строчного rollout test.

Поэтому новый material OS-sandbox delta был явно утверждён пользователем
2026-07-29 только для implementation и repo-local/temp-only verification.
Одобрение не разрешает commit, push, PR либо live-действия; review и delivery
tasks остаются открытыми.

Последующий независимый run
`20260729T192126Z-kanban-os-sandbox-independent-review` снова завершился
`BLOCK`. Он обнаружил незакреплённое descriptor identity окно между parent
anchors и `bwrap`, raw host paths в `provenance.json`, forwarding façade и
почти предельный `runtime_coherence.py`, удалённые security regressions,
overclaim capability probe и непроверенную four-suite validation из-за
read-only review sandbox. Поэтому ниже зафиксирован новый material baseline;
отдельное approval на его implementation получено от Руслана 2026-07-29.

Следующий независимый run
`20260729T224514Z-kanban-remediation-independent-review` дважды подряд
успешно выполнил exact four-suite команду, но снова завершился `BLOCK`.
Зелёные runs подтвердили текущую regression matrix, однако не закрыли
security acceptance. Directory FD закрепляет directory object, но чтение
вложенного regular file внутри него по тому же имени всё ещё видит in-place
изменённые bytes. Поэтому path-reopen remediation не закрыла mutation после
anchor construction. Review также выявил неполный exception-safe ownership
FD при ошибках `write`/`lseek`/seal и фактическое отсутствие измеримого
запаса у 999-строчного rollout test с 40-строчным support façade.

Во время reviewer-only probe source file был временно изменён и затем
побайтово восстановлен; pre/post fingerprints совпали. Этот deviation
исключён из mandatory evidence и не считается implementation change.
Следующий baseline должен проверяться без source mutation.

## Что меняется

- Принять новый независимый thermo verdict `BLOCK`: два author exact
  four-suite run по `140 passed, 0 failed` сохраняются только как
  historical evidence. Completion claims 19.4, 19.6, 19.7, 20.2, 21.2 и
  21.5 отзываются до remediation и accepted independent review; 21.6–21.8,
  delivery и live gates остаются открытыми.
- Заменить однофазный capture на двухфазный bounded inventory → sealed
  acquisition. Inventory descriptor-relative с `O_NOFOLLOW` удерживает
  одновременно только малое ограниченное число временных FD, строит
  topology, identities/digests и exact ELF dependency plan. До создания
  content memfd вычисляются все FD/argv/env/serialized-args budgets и любая
  нехватка завершает операцию fail-closed. Второй проход захватывает sealed
  bytes и повторно сверяет topology/identity/digest; любое изменение
  завершает операцию fail-closed.
- Сделать ELF closure точной и закрытой по умолчанию: отдельно хранить
  `DT_RPATH` и `DT_RUNPATH`, реализовать GNU/Linux precedence и inheritance,
  включая superseding `RUNPATH` для defining object и legacy inheritance
  `RPATH`. `$ORIGIN`, `$LIB`, `$PLATFORM` разрешаются только
  детерминированно из exact runtime/platform; unsupported token,
  relative/empty/unsafe entry или path escape отклоняются до capture.
  Dynamic segment обязан быть bounded, кратен entry size и содержать
  `DT_NULL` внутри segment; string offsets и terminators bounded.
  `DT_NEEDED` принимается только как safe soname без slash, `NUL` и escape.
- Ввести deterministic resource planner до partial sealed acquisition и до
  invocation. Он считает current open FDs, все content entries,
  manifest/loader/`bwrap`/libraries/harness/anchors/probe/prod args FDs и
  явный subprocess/`bwrap` reserve, затем сравнивает сумму с finite
  `RLIMIT_NOFILE`. Байты `argv` + environment сравниваются с
  `SC_ARG_MAX` за вычетом именованного safety margin. Serialized payload
  `bwrap --args` получает отдельный явный constant/configurable cap, хотя
  напрямую не подчиняется `ARG_MAX`; platform, `pass_fds` и `bwrap`
  constraints проверяются до memfd/invocation.
- Добавить независимые literal handcrafted ELF fixtures/oracles, не
  использующие production parser, и load-bearing проверки различий
  `RPATH`/`RUNPATH`, inheritance, token expansion и malformed mutations.
  Добавить low `RLIMIT_NOFILE`, занятые FD, oversized topology/argv/args,
  pre-budget отсутствие sealed memfd acquisition, second-pass mutation,
  отсутствие FD leak и cleanup, который не скрывает primary failure.
- Сохранить exact legacy/canonical wrapper grammar и isolated import-origin
  policy в `scripts/hermes_kanban_mcp_runtime_coherence.py`, а общие
  path/Git/venv primitives вынести в отдельный настоящий common ownership
  module. Точное имя и внутренняя раскладка common module не являются
  контрактом, но state/coherence/orchestration consumers должны импортировать
  primitives непосредственно из единственного owner; forwarding re-export
  façade запрещён. `runtime_coherence.py` должен иметь минимум 100 строк
  запаса до hard limit 1000.
- До любого запуска candidate Python вводится обязательный OS-level
  containment через exact `/usr/bin/bwrap`. Baseline capability probe
  проверяет только базовую работоспособность executable/namespaces и не
  объявляет полный профиль доказанным. Authoritative проверкой является
  реальный production invocation со всем sealed content/data bundle; любая его
  ошибка завершает preflight fail-closed. Python-only либо частичный fallback
  запрещён.
- Минимальный sandbox начинает с пустого mount namespace, монтирует
  candidate/runtime и только явно необходимые `/usr`, `/lib*` read-only,
  создаёт раздельные tmpfs для `HOME`, `HERMES_HOME` и temp, использует
  `--clearenv` с точным allowlist, свежий `/proc`, минимальный `/dev`,
  отдельные user/PID/IPC/UTS/cgroup/network namespaces насколько они
  поддержаны, `--new-session` и `--die-with-parent`. Host sockets не
  пробрасываются. Новые daemon, root privilege, deploy или service unit не
  добавляются.
- Security contract формулируется как отсутствие host-visible side effects.
  PID/network/mount isolation не обязаны запрещать каждый syscall внутри
  sandbox, но MUST не позволять ему воздействовать на host. Существующие
  Python audit/sticky denial и monkeypatch guards остаются вторым слоем и
  диагностическим evidence, а не security boundary.
- Заменить directory-descriptor trust на sealed content bundle. Доверенный
  parent descriptor-relative и с `O_NOFOLLOW` строит полный manifest, читает
  каждый исполняемый/импортируемый regular file candidate source tree, exact
  interpreter, необходимого trusted stdlib/runtime closure и `bwrap` в
  отдельный memfd/data object, затем seal-ит его от записи/изменения размера.
  Anchors и digests строятся только из этих captured bytes. `bwrap` получает
  sealed bundle и созданную из manifest directory/symlink topology, но не
  изменяемый исходный каталог.
- Контракт не обещает защиту до capture: он гарантирует exact captured
  verified bytes от завершения anchor construction до `exec`/import. Capture
  завершается fail-closed при неполном manifest, unsupported file type,
  изменении проверяемого объекта во время чтения или невозможности
  материализовать весь required runtime closure.
- Ввести единый exception-safe FD owner. Каждый `open`/`memfd` регистрируется
  сразу; `_data_fd` закрывает current FD при ошибке write/lseek/seal до
  передачи ownership, partial bundle cleanup закрывает все ранее
  приобретённые FDs. Cleanup error становится отдельной structured
  fail-closed ошибкой, сохраняет primary failure и никогда не скрывается.
- Добавить статический sanitized schema-v2 golden из исторического snapshot и
  wrapper с provenance, исходными SHA-256 и исчерпывающим списком
  sanitization substitutions. Ни один из четырёх fixture files, включая
  `provenance.json`, не может содержать raw `/home/openclaw`. Каждая
  substitution записывает `file/field`, source class, source hash, literal
  replacement, count и reason, не сохраняя raw source value. Payload
  bytes/SHA-256 `manifest.json`, `wrapper.before`, `wrapper.after` и
  snapshot-only semantics остаются неизменными. Источник — snapshot
  `6f8738dc308f909bf1735883344f2fcc12f3cbcd-to-30500cf973a40bb0918d33eb0476c1025e08ac0f`;
  исходные SHA-256: `manifest.json`
  `83db7f0c4cd2a3239e5d52402f6b8b88e1a66ca46ba1daa5677249fcac4a196f`,
  `wrapper.before`
  `17052c7d51307f47f9d3d6826a584114d26a1e57c0a272bc48179fed662c1ab9`,
  `wrapper.after`
  `5e03752f40af19fca3151e6ccb5da182521c7860d6c9ebded8f796ce327aad53`.
  Ожидаемые байты MUST храниться независимо и MUST NOT строиться
  производственным вспомогательным кодом/генератором.
- Разделить switch/runtime-coherence validation и snapshot-only rollback
  loader. Rollback schema v2/v3 не требует source repo, candidate runtime,
  venv или imports и работает при missing/corrupt/dirty candidate, сохраняя
  exact snapshot/hash/current-wrapper и `wrapper.before` guards.
- Принимать только exact allow-listed legacy/canonical wrapper templates:
  корректные shebang, `set`, exports и единственный `exec` с exact argv;
  canonical `cd --` находится непосредственно перед `exec`. Comments-only
  match, missing `exec`, дополнительные команды, redirects и shell control
  operators отклоняются.
- Устранить противоречия schema v2/v3, состава focused helper suite и
  dry-run evidence. `prepare` dry-run сообщает только план и доступные до
  candidate evidence; origin evidence появляется на `prepare --apply` и
  повторяется на `switch` dry-run/apply.
- Расширить temp-only acceptance/bypass matrix: direct
  `subprocess._fork_exec`, `ctypes`/native write и network, signal и
  `resource.prlimit`, подмены интерпретатора через символьные ссылки/TOCTOU,
  nested in-place mutate→candidate import/effect→restore после capture с
  полностью совпадающим forged child evidence, поддельные свидетельства
  дочернего процесса,
  missing/broken `bwrap`, неизменные host canaries и snapshot-only rollback
  без запуска candidate, source/venv/interpreter/import dependencies.
- Аналогично проверить nested in-place mutation trusted stdlib regular file,
  а также exact interpreter и `bwrap` bytes там, где platform позволяет
  воспроизводимый behavioral oracle. Во всех случаях выполняются только
  sealed original bytes либо операция fail-closed; host side-effect
  отсутствует.
- Добавить failure injection на каждой стадии acquisition/capture
  (`open`, `memfd`, чтение/запись, `lseek`, запечатывание, передача
  `digest`/`manifest` и передача вызова), проверку закрытия current и всех ранее
  зарегистрированных FDs, отсутствия leaked FDs и structured cleanup error.
- Вернуть в focused suite четыре удалённые path/security regression:
  existing candidate, existing snapshot, symlink stable wrapper и future
  `candidate`/`snapshot`: сценарий с родительской символической ссылкой.
- Реально разгрузить
  `tests/scripts/test_hermes_kanban_mcp_rollout.py`: общий Git/layout/oracle
  harness переезжает в существующий
  `tests/scripts/hermes_kanban_mcp_test_support.py` как содержательный
  reusable owner без thin forwarding. Измеримый gate:
  rollout test `<=850` строк, support `<400`, каждый source/test `<1000`,
  поведение без изменений.
- Зафиксировать successful rollback как завершённый repair baseline:
  восстановлен exact wrapper SHA-256 `17052c7d...` для snapshot
  `6f8738dc...-to-30500cf...`; process/restart/DB/Kanban/smoke не
  выполнялись.
- Для всех fresh bootstrap и ordinary rollout snapshots использовать только
  `schema_version=3`, `snapshot_kind=bootstrap|rollout` и
  `wrapper_contract=source-cwd-nofile-v2`. Новый bootstrap
  `wrapper.after` строит тот же canonical generator, что и ordinary rollout.
- Любой `schema_version!=3`, а также historical schema-v3
  `source-cwd-v1`, запретить для `switch` до preflight и первого managed
  write/`os.replace`. Эти artifacts остаются отдельным snapshot-only
  rollback input, который восстанавливает exact bytes/mode и не зависит от
  исправности imports candidate. In-place migration отсутствует.
- Для `bootstrap-prepare --apply`, `prepare --apply` и `switch --apply`
  потребовать CLI guard `--expected-wrapper-after-sha256`. Dry-run только
  сообщает вычисленный after SHA-256. Missing/mismatch завершается до первого
  managed write, preflight и `os.replace`; rollback CLI не меняется.
- Exact switch loader повторно проверяет `snapshot_kind`,
  `wrapper_contract`, parsed `ulimit=4096`, hash manifest и actual
  `wrapper.after` bytes. Отдельный plan digest не добавляется: after SHA-256
  плюс exact code-level contract/limit validation достаточны. Planned soft
  limit выводится из разобранного wrapper; manifest shape не расширяется.
- Новый `prepare` строит deterministic `wrapper.after` и до создания snapshot
  выполняет sanitized no-DB import-origin preflight, доказывающий, что
  `hermes_cli.main` и `agent.transports.hermes_kanban_mcp_server`
  импортируются из exact target checkout, а `WRITE_TOOLS` содержит
  `kanban_sync_external_task`.
- `switch` повторяет точные проверки wrapper/hash/runtime/import-origin перед
  atomic replacement; malformed или ambiguous wrapper завершается
  fail-closed до записи.
- Приёмка остается только во временном окружении: fresh bootstrap и rollout
  schema v3, rollout из legacy в canonical, rollout из canonical в canonical,
  совместимость historical rollback, v3 switch/rollback, затенение старым
  `site-packages`, dry-run без записи, точный список tools,
  пять helper test modules, OS-level containment и host-canary oracle,
  rollout test `<=850`, support `<400`, source/test files `<1000`, минимум
  100 строк запаса у
  `runtime_coherence.py`, strict OpenSpec и independent review без `BLOCK`.
  Независимая validation выполняется в `workspace-write` sandbox, но review
  остаётся source-read-only: tests могут писать только temp/cache/evidence.
  Обязательны два последовательных успешных запуска одной exact five-module
  команды для bootstrap, rollout, runtime coherence, runtime sandbox и
  rollout state с `HERMES_TEST_FILE_RETRIES=0`, без retry/`FLAKY` и с
  идентичными pre/post fingerprints.
- Гейты доставки заново разделены: только после нового явного одобрения
  material sealed-bundle baseline разрешаются implementation/tests и новый
  цикл независимой проверки; `commit`/`push`/`PR`
  разрешаются только после accepted review без `BLOCK`. Live
  rollout/wrapper/restart/process replacement/DB остаются отдельным exact
  разрешением и не следуют из planning approval, review или PR.
- Сохранить ранее выполненный bootstrap-helper contract как исторический
  baseline этого change; он не является новым scope repair PR.
- Исторически добавлена в существующий stdlib-only helper одна новая
  dry-run-first команда `bootstrap-prepare`.
- `bootstrap-prepare --apply` эксклюзивно создаёт заранее отсутствующий
  dedicated state root с mode `0700`, причём только по exact path с уже
  существующим проверенным parent.
- Создать внутри root exact detached baseline worktree
  `hermes-kanban-mcp-<SOURCE_COMMIT>` из явно переданного source repo. Не
  требовать ancestor relation между source commit и будущим target: оба
  обязаны быть exact commit objects в source repo.
- Копировать из export runtime только выбранный top-level `.venv` или `venv`;
  не копировать config, `.env`, DB, sessions, runtime state или иные файлы.
- Проверить экспортный манифест как обычный файл без символьных ссылок строго
  внутри экспортированной среды; декодировать его как `UTF-8` и разбирать
  непустые строки `key=value` с уникальными непустыми ключами, без `NUL` и
  повреждённых строк.
- Требовать `source_commit` ровно один раз и проверять, что его значение равно
  явно переданному полному `Git SHA`. Неизвестные ключи разрешать, но их
  значения не выводить и не переносить в снимок.
- Сохранить проверку точного `SHA-256` сырых байтов экспортного манифеста:
  пробный запуск печатает наблюдаемый хэш, а `--apply` требует ожидаемый хэш.
  Также закрепить `SHA-256` и режим интерпретатора, `SHA-256` и режим
  стабильного `wrapper`, а также ровно одну ссылку `wrapper` на
  экспортированную среду.
- Создавать fresh bootstrap snapshot только как schema v3 manifest с
  `snapshot_kind=bootstrap`, `wrapper_contract=source-cwd-nofile-v2`,
  `wrapper.before` и exact canonical-generated `wrapper.after`; stable
  wrapper, процессы, DB, services и network не менять.
- Исторический bootstrap-helper заменил snapshot schema v1 на schema v2 с
  `snapshot_kind=bootstrap|rollout` и общей моделью before/after runtime;
  новый ordinary rollout использует schema v3, а существующий rollout v2
  остаётся только rollback-readable.
  Любой реально встреченный non-v3 artifact может быть только отдельным
  snapshot-only exact bytes/mode rollback input; fresh creation и switch
  запрещены, in-place migration отсутствует.
- `switch` не получает отдельную bootstrap atomic policy: полная validation
  повторно проверяет export/runtime evidence и использует общий atomic
  primitive. `rollback` использует отдельный snapshot-only loader, но тот же
  atomic primitive и не требует export/source/candidate runtime evidence.
- После bootstrap switch обычный `prepare` создаёт schema v3 target
  `hermes-kanban-mcp-<TARGET_COMMIT>` из baseline в том же dedicated root,
  затем `switch` выполняет runtime-coherence audit, а `rollback` работает
  только по exact snapshot/current-wrapper evidence.
- Сохранить fail-closed path policy и exact temp unlink policy; не добавлять
  broad cleanup, recovery delete, `reset`, `clean`, `rmtree` или globs.
- Удержать rollout test `<=850` строк, support `<400`, каждый source/test file
  ниже 1000 строк, минимум 100 строк запаса
  у `runtime_coherence.py` и прямое владение общими path/Git/venv primitives
  отдельным common module без forwarding façade. Helper сохраняет отдельные
  orchestration, snapshot/transition и wrapper/import boundaries; четыре
  helper test files используют только временные каталоги. Тестовый
  `manifest.txt` должен содержать фактические ключи `source_commit`,
  `deployed_utc`, `python_version`, `mcp_version`, `command` и покрывать
  дубликаты, повреждённые строки, `NUL` и несовпадение `source_commit`.

## Возможности

### Новые возможности

- `dedicated-kanban-mcp-surface`: существующая capability расширяется
  безопасным bootstrap переходом от non-Git export runtime к exact immutable
  Git baseline до обычного rollout lifecycle.

### Изменённые возможности

- `dedicated-kanban-mcp-surface`: новый canonical wrapper получает
  process-local finite soft `RLIMIT_NOFILE=4096` до запуска Python. Это
  observable environment contract только нового wrapper kind; исторические
  `source-cwd-v1`/schema-v2 wrappers остаются rollback-readable и
  byte-identical.

Нет отдельных archived capabilities: delta остаётся внутри уже открытого
change и его существующей capability.

## Влияние

- PR #15 (`062f2f0f1f6947830d1b222a3ef470e145a7c34d`) и PR #16
  (`9fcd66651768e3cf220d5cd501efbec5ae3e2550`) уже выполнены; их tasks
  остаются закрытыми.
- Уже выполненный bootstrap-helper PR менял только
  `scripts/hermes_kanban_mcp_rollout.py`,
  `scripts/hermes_kanban_mcp_rollout_state.py`,
  `tests/scripts/test_hermes_kanban_mcp_rollout.py`,
  `tests/scripts/test_hermes_kanban_mcp_bootstrap.py` и artifacts этого
  OpenSpec change.
- После нового approval remediation repair PR может менять только
  `scripts/hermes_kanban_mcp_rollout.py`,
  `scripts/hermes_kanban_mcp_rollout_state.py`, неизбежную минимальную часть
  `scripts/hermes_kanban_mcp_runtime_coherence.py`, пять focused temp-only
  helper test modules, существующие test support/common owners и artifacts
  этого OpenSpec изменения; продуктовые модули,
  dependency metadata, runtime wrapper/state, process, DB и Kanban остаются
  вне scope.
- Production modules, зависимости, DB schema/migrations, connector config,
  stable wrapper, live runtime/state, процессы, services и network не
  меняются.
- Входной live evidence считается зафиксированным, но в planning/PR фазе live
  paths повторно не читаются.
- Merge bootstrap-helper PR не разрешает `bootstrap-prepare --apply`, обычный
  `prepare --apply`, `switch --apply`, process changes или smoke. Для них
  остаётся отдельный post-merge gate по exact dry-run plan.
- Verdict `20260729T224514Z-kanban-remediation-independent-review` и новый
  independent thermo verdict равны `BLOCK`. Два author exact four-suite run
  по `140 passed, 0 failed` остаются historical evidence, но не acceptance.
  Tasks 19.4, 19.6, 19.7, 20.2, 21.2 и 21.5 переоткрыты; 21.6–21.8,
  delivery и live gates остаются открытыми. Новая implementation запрещена
  до повторного явного approval раздела 22.x.

## Minor remediation 23.x: review BLOCK по существующим requirements

Последующий independent review вернул `BLOCK`: external ELF symlink hops не
перепроверяли trusted-root containment, probe actual loader argv обходил
authoritative `SC_ARG_MAX`, а pre-acquisition budget использовал
placeholder/file-only invocation. Эти findings не меняют одобренный scope
22.x и исправляются без нового approval.

Remediation вводит один immutable canonical invocation owner для budget и
execution. Он содержит полные probe/production args, topology,
harness/anchors и symbolic FD roles. Worst-case legal decimal FD width из
finite `RLIMIT_NOFILE` доказывает pre-acquisition upper bound; actual render
тем же spec повторно проверяется перед args memfd/subprocess. Independent,
delivery, commit/push/PR и live gates остаются открытыми.

## Minor remediation 24.x: acquisition peak и final handoff

Latest independent review снова вернул historical `BLOCK`: recursive
acquisition peak был недосчитан, final subprocess handoff не перепроверялся
после создания phase memfd, invocation policy имела duplicated owners, а
exact role order и symlink matrix оставались неполными. Это gaps уже
существующих approved requirements 22.x/23.x, а не material scope change;
новый approval не требуется.

Remediation добавляет в inventory отдельный bound из
`MAX_DIRECTORY_DEPTH` lifecycle, authoritative final handoff непосредственно
перед каждым subprocess, единственного invocation owner и exact ordered role
contracts. Temp-only regressions закрепляют deep low-RLIMIT/no-leak,
late-pressure/no-subprocess и relative multi-hop/escape/dangling/cycle.
Independent, delivery, commit/push/PR и live gates остаются открытыми.

## Minor remediation 25.x: topology coherence перед acquisition

Новый independent review вернул historical `BLOCK` по одному P1 gap уже
одобренных требований. `InventoryPlan` резервировал canonical acquisition
depth, но второй recursive acquisition pass не применял этот предел. При
mutation между проходами лексически ранние regular files могли получить
content memfd до позднего topology mismatch.

Remediation повторно строит canonical inventory непосредственно перед первым
content memfd и сверяет его с approved plan. Сам acquisition независимо
применяет импортированный `MAX_DIRECTORY_DEPTH` до открытия directory на
запрещённой глубине, закрывая post-preflight TOCTOU. Claims 22.3, 22.5, 24.2
и load-bearing часть 24.3 переоткрывались на valid behavioral red и закрыты
только targeted green. Новый approval не требуется; independent, delivery,
commit/push/PR и live gates остаются открытыми.

## Материальная дельта 26.x: process-local NOFILE capacity canonical wrapper

Merged PR #18 получил `CODE VERDICT APPROVE`. В review sandbox finite
`RLIMIT_NOFILE` был `1048576/1048576`, и exact suite дважды завершился
`163 passed`. Однако read-only probe normal HOSTKEY shell и текущих MCP
процессов показал finite soft/hard `1024/1048576`, тогда как sealed plan
требует `1360`; существующий fail-closed при soft `1024` корректен и не
ослабляется.

Observable environment contract меняется только для нового canonical wrapper
kind: до `cd --` и `exec` Python wrapper устанавливает process-local finite
soft limit точной строкой `ulimit -S -n 4096`. Значение `4096` больше
измеренного требования `1360`, даёт примерно трёхкратный запас, остаётся
finite и ниже наблюдавшегося hard limit. Hard limit не повышается, unlimited
не используется; root, systemd, `prlimit` и внешний launcher не требуются.
Стабильный live wrapper, его bytes/mode и процессы этим planning delta не
меняются.

Фраза пользователя «Даю апрув» разрешает подготовить и проверить этот
material delta, но не реализацию. Перед implementation exact baseline
26.x должен быть показан пользователю и получить ещё одно явное approval по
глобальному OpenSpec gate. Ни code/PR/merge, ни это planning approval не
разрешают live prepare/switch, wrapper replacement, process replacement,
MCP smoke, DB, systemd, restart, deploy или network action.

## Remediation baseline 27.x после независимого `BLOCK`

Independent review не принял дельту 26.x как завершённую. Evidence
26.3–26.5 сохраняется только исторически: оно не доказало единый fresh
bootstrap/rollout schema contract, обязательный after-hash CLI guard и
финальную five-module acceptance. Текущий `128 passed` sibling run не
является заменой новой exact suite.

Новый baseline ограничивает implementation следующими файлами после
отдельного approval:

- `scripts/hermes_kanban_mcp_rollout.py`;
- `scripts/hermes_kanban_mcp_rollout_state.py`;
- только неизбежная минимальная правка
  `scripts/hermes_kanban_mcp_runtime_coherence.py`;
- `tests/scripts/test_hermes_kanban_mcp_bootstrap.py`;
- `tests/scripts/test_hermes_kanban_mcp_rollout.py`;
- `tests/scripts/test_hermes_kanban_mcp_runtime_coherence.py`;
- `tests/scripts/test_hermes_kanban_mcp_runtime_sandbox.py`;
- `tests/scripts/test_hermes_kanban_mcp_rollout_state.py`;
- при необходимости только существующие
  `scripts/hermes_kanban_mcp_rollout_common.py` и
  `tests/scripts/hermes_kanban_mcp_test_support.py` как substantive owners.

`runtime_coherence.py` уже содержит `897/900` строк. Новую state/CLI policy
нельзя добавлять туда, кроме минимальной правки canonical generator/parser,
без которой exact code-level validation невозможна. Основная schema,
loader, hash-guard и CLI orchestration policy принадлежит rollout state и
rollout owners. Если line cap потребует extraction, разрешён перенос только
в существующий substantive support/common owner; новый thin wrapper
запрещён.

RLIMIT tests обязаны быть hermetic: отдельный child Python trampoline сам
устанавливает контролируемые soft/hard limits и запускает wrapper. Тесты не
зависят от ambient hard limit или infinity и не используют `preexec_fn`.

Перед code remediation exact baseline должен быть показан пользователю и
получить новое явное approval. Затем обязательны valid red matrix,
implementation, два последовательных exact five-module run с retries `0`,
без `FLAKY` и с идентичными fingerprints, accepted independent review и
строго non-live PR lifecycle. Прежние `25.7`, `26.6` и `26.7+`, а также все
live gates остаются открытыми.
