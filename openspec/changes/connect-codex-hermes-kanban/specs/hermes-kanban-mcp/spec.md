## ADDED Requirements

### Requirement: Режим MCP по умолчанию работает только на чтение

Hermes Kanban MCP server SHALL показывать только инструменты чтения, если он не запущен с явным разрешением на запись.

#### Scenario: Список инструментов по умолчанию доступен только для чтения
- **WHEN** server запускается без `--allow-write`
- **THEN** tool discovery возвращает `kanban_board_status` и `kanban_list_tasks`
- **AND** инструменты записи вроде `kanban_enqueue`, `kanban_claim_next`, `kanban_complete` и `kanban_import_openspec_tasks` не показываются

#### Scenario: Вызовы чтения не мутируют базу данных
- **WHEN** `kanban_board_status` или `kanban_list_tasks` читает существующую доску
- **THEN** server использует SQLite-соединение только для чтения
- **AND** он не инициализирует базу данных и не создает sidecar-файлы WAL, SHM или init-lock

### Requirement: Инструменты записи требуют allow-write

Hermes Kanban MCP server SHALL показывать узкие инструменты записи Kanban только при запуске с `--allow-write`.

#### Scenario: Список инструментов в режиме записи
- **WHEN** server запускается с `--allow-write`
- **THEN** tool discovery включает инструменты чтения и выделенные инструменты записи Kanban
- **AND** посторонние Hermes tools вроде terminal, file, web search и общих Hermes tools не показываются

#### Scenario: Жизненный цикл взятой задачи
- **WHEN** MCP client ставит задачу в очередь, берет следующую готовую задачу, отправляет heartbeat и завершает задачу с возвращенным claim token
- **THEN** задача переходит в `done`
- **AND** запуск задачи сохраняет переданные summary, result и ограниченные metadata

### Requirement: Выбор доски задается в каждом tool call

Hermes Kanban MCP tools SHALL принимать опциональный параметр `board` для выбора пользовательской доски без требования OpenSpec или отдельного server process.

#### Scenario: Явная доска используется для операции
- **WHEN** tool call передает валидный `board` slug
- **THEN** операция выполняется для этой доски
- **AND** отсутствующий или пустой `board` использует текущую Hermes Kanban board

#### Scenario: Невалидная доска отклоняется
- **WHEN** tool call передает невалидный board slug
- **THEN** server возвращает ошибку `invalid_argument`
- **AND** он не создает базу данных доски в рамках read-only validation

### Requirement: Импорт OpenSpec является опциональным и узким

Hermes Kanban MCP server SHALL делать импорт задач OpenSpec доступным только как опциональный helper режима записи для workflow общей доски Руслана.

#### Scenario: Инструмент импорта доступен только в режиме записи
- **WHEN** server запускается без `--allow-write`
- **THEN** `kanban_import_openspec_tasks` не показывается

#### Scenario: Минимальный импорт чеклиста
- **WHEN** `kanban_import_openspec_tasks` получает OpenSpec `tasks.md` со строками чекбоксов вида `- [ ] 1.1 Название`
- **THEN** он импортирует или обновляет Kanban-задачи по стабильному external key
- **AND** он сохраняет русский текст задач с техническими идентификаторами вроде MCP, API, README и file paths

#### Scenario: Повторный импорт сохраняет рабочее состояние
- **WHEN** ранее импортированная задача уже claimed, running, done или иначе операционно изменена в Kanban
- **THEN** повторный импорт обновляет только поля, принадлежащие source: title, body, external key и source path
- **AND** он не сбрасывает поля claim, run, status, result, failure, workflow или heartbeat

### Requirement: Пользовательский текст пишется по-русски

Hermes Kanban MCP server SHALL инструктировать агентов писать пользовательский текст Kanban и OpenSpec на русском языке, разрешая технические идентификаторы на английском.

#### Scenario: Смесь русского и технического английского валидна
- **WHEN** название или тело задачи содержит русский текст и технические идентификаторы вроде `MCP`, `API`, `README`, tool names, library names, code или file paths
- **THEN** инструкции server разрешают такой текст
- **AND** regex-валидация кириллицы не требуется

### Requirement: Операции с live-средой вне области до явного разрешения

Этот change SHALL NOT считать деплой, изменения live DB, настройку profile configuration, изменения systemd, restart или работу с secrets выполненной реализацией.

#### Scenario: Только валидация репозитория
- **WHEN** этот change валидируется в task-owned worktree
- **THEN** tests могут использовать временные базы данных внутри test temp paths
- **AND** live `/home/openclaw/.hermes`, live `kanban.db`, systemd, profile configs, deploy/restart и secrets остаются нетронутыми
