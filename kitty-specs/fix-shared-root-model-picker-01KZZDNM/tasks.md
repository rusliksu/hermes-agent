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
