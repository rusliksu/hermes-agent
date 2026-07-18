## 1. Реализация в репозитории

- [x] 1.1 Добавить выделенный Hermes Kanban MCP server с режимом чтения по умолчанию.
- [x] 1.2 Ограничить инструменты записи запуском `--allow-write`.
- [x] 1.3 Поддержать параметр `board` для отдельных пользовательских досок.
- [x] 1.4 Добавить опциональный импорт OpenSpec `tasks.md` для режима записи.
- [x] 1.5 Зафиксировать русскоязычные пользовательские инструкции без запрета технического английского.

## 2. Тесты и валидация

- [x] 2.1 Покрыть набор инструментов чтения и отсутствие DB mutation в targeted tests.
- [x] 2.2 Покрыть enqueue/claim/heartbeat/complete в режиме записи через MCP stdio.
- [x] 2.3 Покрыть импорт OpenSpec, идемпотентность и сохранение рабочего состояния.
- [x] 2.4 Исправить teardown падающего stdio test через естественное закрытие stdin.
- [x] 2.5 Прогнать `git diff --check`.
- [x] 2.6 Прогнать OpenSpec strict validation локальным `openspec` 1.6.0.
- [x] 2.7 Прогнать targeted tests в одноразовом venv с `pytest==9.0.2`, `pytest-asyncio==1.3.0`, `mcp==1.26.0`.
- [x] 2.8 Прогнать `npx @fission-ai/openspec@1.6.0 validate connect-codex-hermes-kanban --strict`.

## 3. Интеграция с live-средой

- [x] 3.1 Получить отдельное разрешение пользователя на live MCP configuration и первую DB запись.
- [x] 3.2 Подключить MCP server к общей live-доске Руслана без изменения secrets.
- [x] 3.3 Выполнить live smoke после отдельного разрешения и без незапрошенного deploy/restart.

## 4. Фактическая верификация rollout

- [x] 4.1 Проверить backup `/home/openclaw/.hermes/backups/kanban-before-live-mcp-20260718T165906Z.db`, SHA256 `232cec5154c3eef82260a0ec8f06265b60fa51061f49aac2facd0e6d095fdb06`, integrity ok.
- [x] 4.2 Проверить standalone runtime `/home/openclaw/.hermes/mcp/hermes-kanban`, wrapper `run.sh`, `manifest.txt`, source commit `6f8738dc308f909bf1735883344f2fcc12f3cbcd`, `mcp` `1.26.0`.
- [x] 4.3 Проверить local Codex global MCP `hermes-kanban` через credential-free SSH stdio command к `run.sh`.
- [x] 4.4 Проверить MCP flow: `initialize`, `tools/list` с 10 tools, `enqueue`, `claim`, `heartbeat`, `complete`.
- [x] 4.5 Проверить первую live-задачу `t_2e3f153c`: internal status `done`, status label `Готово`, dashboard показывает Default board с 1 task in Done.
- [x] 4.6 Проверить отсутствие gateway restart: `ActiveState=active`, `SubState=running`, `NRestarts=0`, PID unchanged `4081225`.
