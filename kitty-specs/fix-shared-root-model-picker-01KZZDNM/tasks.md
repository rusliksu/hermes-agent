# Рабочие пакеты

## WP01: Full-boundary regression и минимальный ingress fix (P0)

**Цель:** локальный `/model` работает в авторизованной корневой shared-room,
не расширяя глобальные или межпрофильные права.

### Подзадачи

- [x] T001 Добавить full-dispatch RED для root shared-room и сохранить точный
  failure output.
- [x] T002 Добавить отрицательные oracles для `--global`, unauthorized source,
  других slash commands и callback mismatch.
- [x] T003 Внести минимальную правку `_check_slash_access` после RED.
- [x] T004 Выполнить focused/affected suites, Ruff, `py_compile`, diff-check и
  review; создать task-owned commit.

### Критерий завершения

Все сценарии spec зелёные, diff ограничен ingress policy, тестами и mission
artifacts; live не изменён.

### Зависимости

Нет. Пакет выполняется последовательно из-за пересечения теста и production-кода.

## WP02: Прямой `/verbose` и baseline `VERBOSE` (P1)

**Цель:** убрать обязательный цикл из пяти вызовов и сделать подробный прогресс
базовым для всех активных Gurra profiles.

### Подзадачи

- [x] T005 Добавить RED для typed mode, `next`, Telegram picker и shared-room
  trusted adapter.
- [x] T006 Реализовать единый apply path для typed/picker выбора.
- [x] T007 Установить профильный baseline `verbose` во всех активных configs.
- [x] T008 Выполнить focused tests, review, isolated deploy и runtime smoke.

### Критерий завершения

`/verbose` без аргументов показывает выбор, точный аргумент применяет режим
сразу, `next` сохраняет совместимость, а следующий Telegram turn в каждом
активном профиле по умолчанию показывает полные tool calls.

## WP03: Topic-wide model routing и cost-safe fallback (P0)

**Bead:** `tm-ai-loopx-kimi-n1p`

**Цель:** исключить неявный платный GLM и сделать `/model` действительно общим
для всех авторизованных участников одной shared room/topic.

### Подзадачи

- [x] T009 Добавить RED для shared lane key без sender identity и legacy migration.
- [x] T010 Исправить canonical key/read-migrate path без ослабления room/topic isolation.
- [x] T011 Установить Codex/Luna defaults для room profiles и cost-safe fallback order.
- [x] T012 Выполнить focused/affected suites, review, isolated deploy и runtime canary.

### Критерий завершения

Участники одного shared topic используют общий override; другие lanes
изолированы; без override используется Codex/Luna; implicit paid GLM отсутствует.
