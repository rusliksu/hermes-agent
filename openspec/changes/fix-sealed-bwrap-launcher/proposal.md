## Why

На HOSTKEY обычный запуск `bwrap 0.9.0` с полным требуемым профилем
пространств имён проходит, но текущий sealed launcher исполняет `bwrap` как
payload явного `ld-linux` и воспроизводимо завершается на настройке UID map с
`Permission denied`. Из-за этого `import_preflight_session` блокирует
`prepare` до snapshot или замены wrapper, хотя сам профиль поддерживается
хостом.

Независимый security/code review
`20260731T125236Z-sealed-launcher-independent-security-rev` модели
`gpt-5.6-sol` завершился с `verdict=BLOCK`: реализованный baseline не
ограничивает по байтам capture дочернего процесса, не гарантирует post-handoff
verification на всех путях после фактического старта, неполно связывает
проверенную identity с handoff и не имеет достаточных независимых
mutation/security tests. Поэтому ниже фиксируется ещё не одобренный material
delta; до повторного явного approval затрагиваемая реализация не продолжается.

## What Changes

- Заменить explicit-loader форму запуска на обычный kernel-level exec
  канонического `/usr/bin/bwrap`, непосредственно перед передачей управления
  проверенного по типу, владельцу, mode, identity и SHA-256 относительно
  sealed anchor.
- Сохранить sealed immutable `bwrap --args`, полный namespace/mount profile,
  пустое окружение launcher, отсутствие credential/env inheritance и
  fail-closed поведение probe и production preflight.
- Удалить из canonical launcher argv роли `ld-linux`, `--inhibit-cache` и
  `--preload`; пересчитать symbolic/actual argv и FD budgets из того же
  immutable invocation spec.
- Добавить bounded allow-listed классификацию `stderr`, чтобы evidence
  различало отказ настройки UID map без отражения произвольного дочернего
  вывода, путей, окружения или секретов. Само allow-listed mapping остаётся
  additive diagnostic, но его byte-bounded input contract входит в material
  delta ниже.
- Ограничить фактически захватываемые `stdout` и `stderr` жёсткими byte caps:
  при переполнении останавливать и дочищать child fail-closed, возвращать
  только безопасный reason code и никогда не передавать классификатору
  неполные либо unbounded данные.
- Выполнять post-handoff verification после каждого фактического старта child:
  при normal exit, timeout и `OSError`/ошибке запуска после старта, сохраняя
  одновременно primary failure и отдельное secondary verification evidence.
- Проверять и фиксировать `uid`, `gid`, `mode`, `device`, `inode`, `size` и
  SHA-256; провести проверенный executable descriptor через handoff, сохранив
  literal canonical `/usr/bin/bwrap` как kernel exec target и явно ограничив
  гарантии этого path-based trade-off.
- Добавить unit/invariant и focused launcher tests, HOSTKEY
  loader-vs-direct/UID-map integration gate и повторный existing
  five-module regression gate.
- Добавить независимые mutation/security tests literal canonical target,
  syscall-level verifier doubles, раздельных digest/identity и
  `uid`/`gid`/`mode`/`O_NOFOLLOW` мутаций, timeout/post-check и реального
  переполнения capture; устаревшие explicit-loader assumptions удалить или
  явно пометить superseded.
- Сохранить доставку по этапам: сначала только task-owned code PR; live
  `prepare`, `switch` и замена dedicated process остаются отдельными exact
  approval gates. Code gate не разрешает deploy или restart.

## Capabilities

### New Capabilities

- `sealed-bwrap-launcher`: контракт проверенного kernel-level запуска
  bubblewrap, sealed arguments, диагностик, тестов и раздельных delivery/live
  gates.

### Modified Capabilities

Нет изменений уже архивированных capabilities. После явного approval эта
отдельная capability нормативно supersede только launcher-specific требования
активного change `expose-external-sync-on-kanban-mcp`, требовавшие исполнять
sealed memfd-копию `bwrap` через explicit loader; сам существующий change в
этом planning run не изменяется.

## Impact

- Будущая реализация: `scripts/hermes_kanban_mcp_invocation.py` и
  `scripts/hermes_kanban_mcp_os_sandbox.py`; только при доказанной
  необходимости — минимальные согласованные правки owners sealed bundle и
  resource planning без изменения candidate-content contract.
- Будущие tests: новый focused launcher module и существующие пять модулей
  `tests/scripts/test_hermes_kanban_mcp_*`.
- Публичные Hermes/MCP API, зависимости, schema snapshot/wrapper, БД,
  credentials, network policy, sysctl, AppArmor, RLIMIT, systemd и сервисы не
  меняются.
- Change является material из-за изменения executable trust/handoff
  primitive; настоящий review delta дополнительно меняет capture, failure
  evidence, descriptor handoff и security-test contract. Delta требует нового
  явного approval до затрагиваемой implementation.

## Критерии приёмки material delta

- Capture каждого child остаётся в заданных byte caps даже при реальном
  непрерывном выводе; overflow всегда fail-closed и не раскрывает raw bytes.
- Post-handoff verification доказан для normal exit, timeout и ошибок после
  фактического старта, а primary и secondary failures доступны одновременно.
- Проверки отдельно ловят мутации `uid`, `gid`, `mode`, `device/inode/size` и
  digest; утверждённый descriptor существует через handoff, а literal exec
  target остаётся `/usr/bin/bwrap`.
- Независимый mutation/security suite и повторный review завершаются без
  `BLOCK`; до этого code/delivery/live gates закрыты.
