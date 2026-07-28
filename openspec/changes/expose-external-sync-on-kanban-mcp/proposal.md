## Почему

PR #15 доставил выделенную Kanban MCP поверхность, а PR #16 — guarded
dry-run-first helper `prepare/switch/rollback`. Live rollout после них не
выполнялся. Зафиксированный live evidence показал новую исходную границу:
текущий export runtime `/home/openclaw/.hermes/mcp/hermes-kanban` не является
Git worktree, поэтому обычный `prepare`, который требует exact clean Git
runtime, не может безопасно создать первый immutable baseline.
Предоставленный экспортный `manifest.txt` использует формат строк
`key=value` в `UTF-8`, а не `JSON`.

Нужен отдельный bootstrap-helper PR без live rollout. Он должен превратить
только доказанный export runtime в exact detached Git baseline, сохранить
достаточный rollback snapshot и оставить stable wrapper неизменным. После
отдельно одобренного live bootstrap switch существующий обычный lifecycle
сможет выполнить `prepare` от baseline к target.

## Что меняется

- Добавить в существующий stdlib-only helper одну новую dry-run-first команду
  `bootstrap-prepare`.
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
- Создать bootstrap snapshot, содержащий только schema v2 manifest,
  `wrapper.before` и exact `wrapper.after`; stable wrapper, процессы, DB,
  services и network не менять.
- Минимально заменить snapshot schema v1 на schema v2 с
  `snapshot_kind=bootstrap|rollout` и общей моделью before/after runtime.
  Backward compatibility для schema v1 не добавлять: по входному evidence
  live state root, baseline, target и snapshots отсутствуют; implementation
  обязана отдельно проверить отсутствие v1 live snapshots до любого live
  apply.
- Существующие `switch/rollback` не получают отдельную bootstrap policy:
  они читают оба вида schema v2 snapshot через один validator и один atomic
  примитив замены.
- Для bootstrap snapshot `switch/rollback` каждый раз повторно проверяют
  хэш манифеста экспорта/`source_commit`, данные `venv` экспорта, чистоту
  baseline Git worktree и baseline venv evidence.
- После bootstrap switch обычный `prepare` создаёт target
  `hermes-kanban-mcp-<TARGET_COMMIT>` из baseline в том же dedicated root,
  затем существующие `switch/rollback` работают по обычному rollout snapshot.
- Сохранить fail-closed path policy и exact temp unlink policy; не добавлять
  broad cleanup, recovery delete, `reset`, `clean`, `rmtree` или globs.
- Удержать каждый source/test file ниже 1000 строк. Разделить helper только по
  реальной ownership boundary: executable orchestration отдельно, snapshot
  schema/validation/atomic transition отдельно. Bootstrap tests вынести в
  отдельный файл тестов, использующий только временные каталоги. Тестовый
  `manifest.txt` должен содержать фактические ключи `source_commit`,
  `deployed_utc`, `python_version`, `mcp_version`, `command` и покрывать
  дубликаты, повреждённые строки, `NUL` и несовпадение `source_commit`.

## Возможности

### Новые возможности

- `dedicated-kanban-mcp-surface`: существующая capability расширяется
  безопасным bootstrap переходом от non-Git export runtime к exact immutable
  Git baseline до обычного rollout lifecycle.

### Изменённые возможности

Нет отдельных archived capabilities: delta остаётся внутри уже открытого
change и его существующей capability.

## Влияние

- PR #15 (`062f2f0f1f6947830d1b222a3ef470e145a7c34d`) и PR #16
  (`9fcd66651768e3cf220d5cd501efbec5ae3e2550`) уже выполнены; их tasks
  остаются закрытыми.
- Bootstrap-helper PR меняет только
  `scripts/hermes_kanban_mcp_rollout.py`,
  `scripts/hermes_kanban_mcp_rollout_state.py`,
  `tests/scripts/test_hermes_kanban_mcp_rollout.py`,
  `tests/scripts/test_hermes_kanban_mcp_bootstrap.py` и artifacts этого
  OpenSpec change.
- Production modules, зависимости, DB schema/migrations, connector config,
  stable wrapper, live runtime/state, процессы, services и network не
  меняются.
- Входной live evidence считается зафиксированным, но в planning/PR фазе live
  paths повторно не читаются.
- Merge bootstrap-helper PR не разрешает `bootstrap-prepare --apply`, обычный
  `prepare --apply`, `switch --apply`, process changes или smoke. Для них
  остаётся отдельный post-merge gate по exact dry-run plan.
