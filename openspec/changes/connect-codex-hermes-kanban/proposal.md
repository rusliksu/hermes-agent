## Зачем

Руслану нужна общая Kanban-доска, через которую Codex и Hermes могут безопасно видеть одну очередь работ и передавать задачи без доступа к лишним инструментам Hermes. Интеграция реализована как узкий MCP-адаптер; change фиксирует контракт, границы безопасности и уже выполненный live rollout.

## Что меняется

- Добавляется узкий Hermes Kanban MCP server поверх stdio: по умолчанию доступны только инструменты чтения статуса доски и списка задач.
- Инструменты записи доступны только при явном запуске с `--allow-write`.
- Все инструменты принимают опциональный параметр `board`, чтобы отдельные пользовательские доски могли работать без общей доски Руслана.
- Импорт OpenSpec `tasks.md` доступен только в режиме записи и является опциональным рабочим процессом для общей доски Руслана, а не обязательным требованием для отдельных пользователей.
- Пользовательский текст задач, причин блокировки, результатов и OpenSpec-артефактов должен быть на русском языке; технические идентификаторы могут оставаться на английском.
- Live rollout выполнен после явного разрешения пользователя: standalone runtime, локальная Codex MCP-регистрация и первая проверочная задача на общей доске.

## Возможности

### Новые возможности

- `hermes-kanban-mcp`: Узкий MCP-доступ к Hermes Kanban для общей доски Руслана и отдельных пользовательских досок.

### Измененные возможности

- Нет.

## Влияние

- Код: `agent/transports/hermes_kanban_mcp_server.py`, `hermes_cli/subcommands/mcp.py`, `hermes_cli/kanban_openspec.py`, `hermes_cli/kanban_db.py`.
- Тесты: targeted-проверки MCP stdio и импорта OpenSpec.
- Системы: standalone runtime развернут в `/home/openclaw/.hermes/mcp/hermes-kanban` с wrapper `run.sh`, `manifest.txt`, source commit `6f8738dc308f909bf1735883344f2fcc12f3cbcd` и `mcp` `1.26.0`.
- Live backup перед первой записью: `/home/openclaw/.hermes/backups/kanban-before-live-mcp-20260718T165906Z.db`, SHA256 `232cec5154c3eef82260a0ec8f06265b60fa51061f49aac2facd0e6d095fdb06`, integrity ok.
- Local Codex global MCP зарегистрирован как `hermes-kanban` через credential-free SSH stdio command к `run.sh`.
- Первая live-задача `t_2e3f153c` завершена; dashboard показывает Default board с одной задачей в Done.
- Gateway не рестартовал: `ActiveState=active`, `SubState=running`, `NRestarts=0`, PID остался `4081225`.
- Текущая синхронизация меняет только OpenSpec artifacts; production code/tests/live/config, secrets, commit и push остаются вне области.
