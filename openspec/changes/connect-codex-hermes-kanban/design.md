## Контекст

Hermes уже имеет локальную SQLite Kanban-доску и CLI-команды для работы с ней. Codex нужен не полный Hermes tool surface, а маленький MCP-интерфейс для совместной очереди: читать безопасные метаданные задач, а при явном разрешении брать/создавать/закрывать задачи.

Ограничения change:
- работа только в task-owned worktree;
- текущая синхронизация OpenSpec не меняет production code/tests/live/config, systemd, profile configs, deploy/restart или secrets;
- live rollout уже выполнен оркестратором после явного разрешения пользователя на live MCP configuration и первую DB запись;
- отдельные пользователи не обязаны использовать OpenSpec;
- импорт OpenSpec нужен как удобство для доски Руслана, но не как общий обязательный рабочий процесс.

## Цели и нецели

**Цели:**
- Дать Codex и Hermes общий узкий MCP-контракт для Kanban.
- Сохранить режим чтения по умолчанию без инициализации или мутации DB при чтении.
- Разрешить операции записи только через `--allow-write`.
- Поддержать параметр `board` для отдельных досок.
- Сделать пользовательские формулировки русскоязычными без regex-запрета технического английского.
- Зафиксировать фактический live rollout: standalone runtime, credential-free SSH stdio registration, backup, первый live task и отсутствие gateway restart.

**Нецели:**
- Не выполнять дополнительные live-изменения при синхронизации OpenSpec artifacts.
- Не изменять production code/tests/live/config, systemd units, profile configs или secrets.
- Не выполнять commit, push, restart, deploy или destructive rollback.
- Не делать OpenSpec обязательным для всех пользователей.
- Не расширять production transport без необходимости.

## Решения

1. MCP server остается отдельным narrow adapter.
   - Выбор: `hermes mcp serve-kanban` запускает выделенный server с фиксированным набором Kanban tools.
   - Альтернатива: открыть существующий общий Hermes MCP/tools surface. Отклонено, потому что это увеличивает права и tool footprint для простой очереди.

2. Режим чтения является поведением по умолчанию.
   - Выбор: инструменты чтения используют SQLite `mode=ro`, `immutable=1` и `PRAGMA query_only=ON`, не проходя через write/init helpers.
   - Альтернатива: использовать общий `kanban_db.connect()`. Отклонено, потому что путь чтения может создать sidecar/init-файлы.

3. Режим записи включается только флагом `--allow-write`.
   - Выбор: инструменты записи регистрируются только при старте с флагом.
   - Альтернатива: runtime-параметр на каждом tool call. Отклонено, потому что tool discovery уже должен отражать доступные права.

4. `board` остается параметром tool call, а не требованием отдельного server instance.
   - Выбор: пустой `board` означает текущую доску, явный slug выбирает отдельную доску.
   - Альтернатива: один MCP server на доску. Отклонено как лишняя конфигурация для текущего scope.

5. Импорт OpenSpec ограничен минимальным форматом чеклиста.
   - Выбор: импортировать только строки вида `- [ ] 1.1 Название`, делать upsert по stable `external_key`, не удалять исчезнувшие строки и не менять рабочее состояние claim/run.
   - Альтернатива: полноценный Markdown/OpenSpec parser. Отклонено как лишнее для текущей интеграции.

6. Live Codex MCP entry использует credential-free SSH stdio к standalone runtime.
   - Выбор: local Codex global MCP зарегистрирован как `hermes-kanban` и запускает wrapper `/home/openclaw/.hermes/mcp/hermes-kanban/run.sh` без передачи credentials в OpenSpec artifacts.
   - Альтернатива: хранить credentials или runtime command details в change artifacts. Отклонено, потому что OpenSpec должен фиксировать контракт и проверенные факты без секретов.

7. Standalone runtime отделен от gateway process.
   - Выбор: runtime размещен в `/home/openclaw/.hermes/mcp/hermes-kanban`, содержит `run.sh`, `manifest.txt`, source commit `6f8738dc308f909bf1735883344f2fcc12f3cbcd` и `mcp` `1.26.0`.
   - Альтернатива: подключать adapter через gateway restart/deploy. Отклонено для rollout, потому что проверенный MCP stdio path не требовал gateway restart.

## Риски и компромиссы

- Ограниченные read tools не возвращают поля body/result/path -> для глубокого анализа нужна отдельная write-capable или CLI-сессия.
- Невалидный `board` slug может создать путаницу у агента -> server возвращает validation error и не создает доску при read-only validation.
- Узкий OpenSpec importer игнорирует сложные Markdown-структуры -> checklist workflow остается предсказуемым, а сложные случаи решаются вручную.
- Live runtime теперь существует вне repo worktree -> rollback разделен на локальное снятие Codex MCP-регистрации и ручной destructive runtime rollback.
- Повторный gateway restart мог бы изменить live-состояние без необходимости -> rollout зафиксирован как no-restart: `ActiveState=active`, `SubState=running`, `NRestarts=0`, PID unchanged `4081225`.

## План перехода и отката

Выполненный rollout:

1. Получено явное разрешение пользователя на live MCP configuration и первую DB запись.
2. Перед записью создан backup `/home/openclaw/.hermes/backups/kanban-before-live-mcp-20260718T165906Z.db`; SHA256 `232cec5154c3eef82260a0ec8f06265b60fa51061f49aac2facd0e6d095fdb06`; integrity ok.
3. Развернут standalone runtime `/home/openclaw/.hermes/mcp/hermes-kanban` с wrapper `run.sh`, `manifest.txt`, source commit `6f8738dc308f909bf1735883344f2fcc12f3cbcd`, `mcp` `1.26.0`.
4. Local Codex global MCP зарегистрирован как `hermes-kanban` через credential-free SSH stdio command к `run.sh`.
5. Gateway не рестартовал: `ActiveState=active`, `SubState=running`, `NRestarts=0`, PID unchanged `4081225`.
6. Smoke прошел полный MCP flow: `initialize`, `tools/list` с 10 tools, `enqueue`, `claim`, `heartbeat`, `complete`.
7. Первая live-задача `t_2e3f153c` с названием `Проверить совместную работу Codex и Hermes через общую Kanban-доску` завершена; internal status `done`, status label `Готово`; dashboard показывает Default board с 1 task in Done.

Откат:

1. Локальный rollback Codex MCP-регистрации: `codex mcp remove hermes-kanban`.
2. Ручной destructive rollback runtime допускается только отдельным явным действием: удалить `/home/openclaw/.hermes/mcp/hermes-kanban` и при необходимости восстановить live DB из backup. В рамках этой синхронизации не выполнять.
3. Repo rollback для OpenSpec/code history остается обычным revert в branch; commit/push в этой задаче не выполняются.
